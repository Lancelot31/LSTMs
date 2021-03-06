
# coding: utf-8

# In[ ]:

#get_ipython().magic('matplotlib inline')

import gym
import itertools
import numpy as np
import os
import random
import sys
import psutil
import tensorflow as tf
import datetime
import pickle
from shutil import copyfile
import h5py

from collections import namedtuple

# Build the name of the folder where we'll store the results
basename = "DQN_LSTM_BlocksWorld"
suffix = datetime.datetime.now().strftime("%y%m%d_%H%M%S")
filename = "_".join([basename, suffix]) # e.g. 'BlocksWorld_120508_171442'

lstm_vis_config = "./lstmvis/lstm.yml"
# Write the path to the repository from dennybritz, we'll use some helper functions
if "../reinforcement-learning/lib" not in sys.path:
  sys.path.append("../reinforcement-learning/lib")

# Plotting is a helper module from dennybritz in the repository mentioned above
import plotting

#sys.path.append("/Users/rubengarzon/Documents/Projects/phD/Repo/gym")


# In[ ]:

#Define the parameters of the problem and the LSTM
#Number of blocks in the blocksworld environment
numBlocks = 2

#Number of steps to feed the LSTM
n_steps = 8

#Defines the dimension of the input of the LSTM, we use current state and the goal state as the input
#For example [0,1] [2,0] (initial state,goal state) has dimension 2*2
n_input = numBlocks*2

#Number of hidden units in the lstm
n_hidden = 64

#Dimensions of the output: [block to be moved][destination of the block]
#block to be moved has numBlocks length, and destination of the block has 
#numBlocks+1 because the block can be moved either on top of another block(numBlocks) but
#also on top of the table(+1)
n_output = numBlocks*(numBlocks+1)

# In[ ]:
#How many possible actions we have in the environment. We will encode the actions as an integer.
VALID_ACTIONS = np.array(range(n_output))


# In[ ]:

class StateProcessor():
    """
    At this time the state processor is not used, but we keep it in case we need it in the future
    """
    def __init__(self):
        # Build the Tensorflow graph
        with tf.variable_scope("state_processor"):
            self.input_state = tf.placeholder(tf.float32)
            self.output = self.input_state

    def process(self, sess, state):
        """
        Args:
            sess: A Tensorflow session object
            state: 

        Returns:
            The same as the input
        """
        return sess.run(self.output, { self.input_state: state })



# In[ ]:

class Estimator():
    """Q-Value Estimator neural network.

    This network is used for both the Q-Network and the Target Network.
    """

    # RNN output node weights and biases

    def __init__(self, scope="estimator", summaries_dir=None):
        self.scope = scope
        # Writes Tensorboard summaries to disk
        self.summary_writer = None
        with tf.variable_scope(scope):
            # Build the graph
            self._build_model()
            if summaries_dir:
                summary_dir = os.path.join(summaries_dir, "summaries_{}".format(scope))
                if not os.path.exists(summary_dir):
                    os.makedirs(summary_dir)
                self.summary_writer = tf.summary.FileWriter(summary_dir)
        # The following variables will be used for LSTM Visualization using LSTMVis package
        self.all_steps = []
        self.all_states = []        
        self.all_outputs = []
        self.all_hid_states = []
        self.all_predictions = []
    
    def add_sessionGraph(self,sess):
        """
        Visualize the graph using tensorboard
        """
        self.summary_writer.add_graph(sess.graph)

    def _build_model(self):
        
        """
        Builds the Tensorflow graph.
        """
        weights = {
            'out': tf.Variable(tf.random_normal([n_hidden, n_output]))
            }
        biases = {
            'out': tf.Variable(tf.random_normal([n_output]))
            }
 
        # Dimension of X_pl is Batch size x time steps x features(or input dimensions).
        self.X_pl = tf.placeholder(shape=[None,n_steps,n_input],dtype = tf.float32,name = "X")
        # The TD target value        
        self.y_pl = tf.placeholder(shape=[None], dtype=tf.float32, name="y")
        # Integer id of which action was selected
        self.actions_pl = tf.placeholder(shape=[None], dtype=tf.int32, name="actions")
             
        batch_size = tf.shape(self.X_pl)[0]

        # Define a lstm cell with tensorflow
        lstm_cell = tf.contrib.rnn.BasicLSTMCell(n_hidden, forget_bias=1.0)

        # Get lstm cell output
        # Creates an unrolled graph
        # outputs shape is batch_size x n_steps x n_hidden
        # states_tuple[0] contains the cell states, shape is batch_size x n_hidden
        # states_tuple[1] contains the hidden states shape is batch size x n_hidden
        outputs, states_tuple = tf.nn.dynamic_rnn(lstm_cell, self.X_pl, dtype=tf.float32)
        #print (outputs.shape)       
        #Store information for LSTMVis
        self.cell_states = states_tuple[0] #(c) (hidden state)
        self.hidden_states = states_tuple[0] #(c) (hidden state)
#        self.cell_states = states_tuple[1] #(h) output
        #print (self.hidden_states.shape)
        #print (self.cell_states.shape)
        #We need to store the input of the network to identify every time step in the
        #LSTMVisualization
        #tf.slice(input, begin,size,...)
        #We take the first element of the batch only, predict will always be called with batch_size=1
        #We take the last step
        #We take the complete input of the network
        #Then we store the value of the input of the network in the current state
        self.current_st = tf.reshape(tf.slice(self.X_pl,[0,n_steps-1,0],[1,1,n_input]),[-1,n_input])

        #We just want the output of the last step
        #perm or axis = [1,0,2]
        #Shape before transpose is batch_size x n_steps x n_hidden
        #Shape after transpose is n_steps x batch_size x n_hidden
        val = tf.transpose(outputs, [1, 0, 2])
        #Then we take the last of the steps n_steps -1
        last = tf.gather(val, int(val.get_shape()[0]) - 1)

        #Shape of last is (batch_size,n_hidden)
        self.outputs = last
        
        # Linear activation, using rnn inner loop last output
        # Shape of self.predictions is batch_size x n_output
        self.predictions = tf.matmul(last, weights['out']) + biases['out']
        print (self.predictions.shape)
        # Get the predictions for the chosen actions only
        #tf.range returns a 1-D tensor starting at 0 and ending in batch_size
        #If n_outputs is 6 for example, we get a 1-D tensor like 0,6,12,18, ...
        #If self.actions_pl = 4 for the first item in the batch and 3 for the second 
        #We get something like 0+4 6+3 ... in gather_indices
        gather_indices = tf.range(batch_size) * tf.shape(self.predictions)[1] + self.actions_pl

        #tf.gather will slice only the predictions for the actions according to gather_indices        
        self.action_predictions = tf.gather(tf.reshape(self.predictions, [-1]), gather_indices)

        # Calcualte the loss
        self.losses = tf.squared_difference(self.y_pl, self.action_predictions)
        self.loss = tf.reduce_mean(self.losses)

        # Optimizer Parameters from original paper
        #self.optimizer = tf.train.RMSPropOptimizer(0.0025, 0.99, 0.0, 1e-6)
#        self.optimizer = tf.train.RMSPropOptimizer(0.00001, 0.99, 0.0, 1e-6)
        self.optimizer = tf.train.AdamOptimizer(0.005)
        #self.optimizer = tf.train.AdagradOptimizer(0.001)
        #self.optimizer = tf.train.RMSPropOptimizer(0.001)
        #self.optimizer = tf.train.RMSPropOptimizer(0.0025, 0.99, 0.0, 1e-6)
#        self.optimizer = tf.train.RMSPropOptimizer(0.0025, 0.99, 0.0, 1e-6)
        self.train_op = self.optimizer.minimize(self.loss, global_step=tf.contrib.framework.get_global_step())

        # Summaries for Tensorboard
        self.summaries = tf.summary.merge([
            tf.summary.scalar("loss", self.loss),
            tf.summary.histogram("loss_hist", self.losses),
            tf.summary.histogram("q_values_hist", self.predictions),
            tf.summary.scalar("max_q_value", tf.reduce_max(self.predictions)),
            tf.summary.histogram("out_weights", weights['out']),
            tf.summary.histogram("out_biases", biases['out'])
        ])

    def predict(self, sess, s,save_cell_states=False):
        """
        Predicts action values.

        Args:
          sess: Tensorflow session
          s: State input of shape [batch_size, 4, 160, 160, 3]
          s: State input of shape [batch_size, n_steps, n_input]

        Returns:
          Tensor of shape [batch_size, NUM_VALID_ACTIONS] containing the estimated 
          action values.
        """
        #print ('Calling predict')
        #print (save_cell_states)
        #print (self.predictions)
#        return sess.run(self.predictions, { self.X_pl: s })
        pred,cell_state,curr_state,outs,hid_state = sess.run([self.predictions, self.cell_states,self.current_st,self.outputs,self.hidden_states], { self.X_pl: s })
        if (save_cell_states == True):
            if len(self.all_states) == 0:
                self.all_states = np.array(cell_state)
            else:
                self.all_states = np.vstack((self.all_states, np.array(cell_state)))
                #Take the last step in the sequence of steps
            if (len(self.all_steps) == 0):
                self.all_steps = np.array(curr_state)
            else:
                self.all_steps = np.vstack((self.all_steps, np.array(curr_state)))
            if (len(self.all_outputs) == 0):
                self.all_outputs = np.array(outs)
            else:
                self.all_outputs = np.vstack((self.all_outputs, np.array(outs)))
            if (len(self.all_hid_states) == 0):
                self.all_hid_states = np.array(hid_state)
            else:
                self.all_hid_states = np.vstack((self.all_hid_states, np.array(hid_state)))    
            if (len(self.all_predictions) == 0):
                self.all_predictions = np.array(pred)
            else:
                self.all_predictions = np.vstack((self.all_predictions, np.array(pred)))
            
        #print (self.all_states)
        #print (self.all_outputs)
        return (pred)

    def update(self, sess, s, a, y):
        """
        Updates the estimator towards the given targets.

        Args:
          sess: Tensorflow session object
          s: State input of shape [batch_size, 4, 160, 160, 3]
          s: State input of shape [batch_size, n_steps, n_input]
          a: Chosen actions of shape [batch_size]
          y: Targets of shape [batch_size]

        Returns:
          The calculated loss on the batch.
        """
        feed_dict = { self.X_pl: s, self.y_pl: y, self.actions_pl: a }
        summaries, global_step, _, loss = sess.run(
            [self.summaries, tf.contrib.framework.get_global_step(), self.train_op, self.loss],
            feed_dict)
        if self.summary_writer:
            self.summary_writer.add_summary(summaries, global_step)
        return loss

    def state_array_to_integer_array (self,state_array):
        #This implementation only works for less than 10 blocks
        #print (state_array)
        #print (type(state_array))
        state_array_list = []
        
        for x in state_array:
            ret = ""
            for digit in x:
                ret += str(int(digit))
            state_array_list.append(int(ret))
        return state_array_list
    
    def state_to_integer (self,state_array):
        #This implementation only works for less than 10 blocks
        ret = ""
        for x in state_array:
            ret += str(int(x))
        return int(ret)
    
                                    
    def state_to_string (self,state_array):
        #This implementation only works for less than 10 blocks
        ret = ""
        for digit in state_array:
            ret += str(digit)          
        return ret
    
    def cartesian(self,*arrays):
        mesh = np.meshgrid(*arrays)  # standard numpy meshgrid
        dim = len(mesh)  # number of dimensions
        elements = mesh[0].size  # number of elements, any index will do
        flat = np.concatenate(mesh).ravel()  # flatten the whole meshgrid
        reshape = np.reshape(flat, (dim, elements)).T  # reshape and transpose
        return reshape
    
    def generate_dictionary_file (self):
        global numBlocks
        ret = ""
        x = np.arange(n_input)
        if (numBlocks == 2):
            a = self.cartesian(x, x, x, x)
        if (numBlocks ==3):
            a = self.cartesian(x, x, x, x, x, x)
        if (numBlocks ==4):
            a = self.cartesian(x, x, x, x, x, x, x, x)
        if (numBlocks ==5):
            a = self.cartesian(x, x, x, x, x, x, x, x, x, x)
        for i in a:
            ret += self.state_to_string(i) + " " + str(self.state_to_integer(i)) + "\n"
        return ret
                        
    def save_states(self,folder):
        #stat = np.reshape(self.all_states, (-1, self.all_states.shape[2]))
        lstm_vis_config
        os.makedirs(folder)
        f = h5py.File(folder + "/states.hdf5", "w")
        #print ("Save states")
        #print (len(self.all_states))
        #print (self.all_states[0])
#        f["states1"] = self.all_states
        #print (len(self.all_outputs))
        #print (self.all_outputs[0])
        f["outputs"] = self.all_outputs
#        f["hidden1"] = self.all_hid_states
        f["hidden1"] = self.all_hid_states
        f["predictions"] = self.all_predictions
        f.close()
        #The contents of train.h5 must be a list of the input at the different steps
        f = h5py.File(folder + "/train.hdf5", "w")
        train = self.state_array_to_integer_array(self.all_steps)
        #print (train[0])
        #print (len(train))
        f["word_ids"] = train        
        f.close()
        data = self.generate_dictionary_file()
        out = open(folder + "/train.dict", "w")
        out.write(data)
        out.close()
        copyfile(lstm_vis_config, folder+"/lstm.yml")

# In[ ]:

# For Testing....

#==============================================================================
# tf.reset_default_graph()
# global_step = tf.Variable(0, name="global_step", trainable=False)
# 
# e = Estimator(scope="test")
# sp = StateProcessor()
# 
# with tf.Session() as sess:
#     sess.run(tf.global_variables_initializer())
#     
#     # Example observation batch
#     observation = env.reset()
#     
#     replay_memory = []
#     
#     for i in range(18):
#         next_action = [random.randint(0,numBlocks-1),random.randint(0,numBlocks)] 
#         observation, reward, done, empty = env.step(next_action)
#         replay_memory.append(observation)        
# 
#     observations = np.array(replay_memory)
#     observations = observations.reshape(-1,n_steps,n_input)
#     # Test Prediction
#     res = e.predict(sess, observations)
#     print (res)
#     print (res.shape)
# 
#     # Test training step
#     y = np.array([10.0, 10.0, 10.0,10.0,10.0,10.0])
#     a = np.array([1,3,5,7,9,11])
#     print(e.update(sess, observations, a, y))
#==============================================================================


# In[ ]:

class ModelParametersCopier():
    """
    Copy model parameters of one estimator to another.
    """
    
    def __init__(self, estimator1, estimator2):
        """
        Defines copy-work operation graph.  
        Args:
          estimator1: Estimator to copy the paramters from
          estimator2: Estimator to copy the parameters to
        """
        e1_params = [t for t in tf.trainable_variables() if t.name.startswith(estimator1.scope)]
        e1_params = sorted(e1_params, key=lambda v: v.name)
        e2_params = [t for t in tf.trainable_variables() if t.name.startswith(estimator2.scope)]
        e2_params = sorted(e2_params, key=lambda v: v.name)

        self.update_ops = []
        for e1_v, e2_v in zip(e1_params, e2_params):
            op = e2_v.assign(e1_v)
            self.update_ops.append(op)
            
    def make(self, sess):
        """
        Makes copy.
        Args:
            sess: Tensorflow session instance
        """
        sess.run(self.update_ops)


# In[ ]:
    
    
        
def computePreviousStates(replay_memory,seq_length):
    prev_states_current = []
    prev_states_next = []
    if (len(replay_memory) > seq_length):
        n = seq_length    
    else:    
        n = len(replay_memory)    
        #while (n > 0 and (replay_memory[-n].done == False)):
    while (n > 0):
        #if (computeNextStates == True):
        prev_states_next.append(replay_memory[-n].next_state)
        #else:
        prev_states_current.append(replay_memory[-n].state)
        n = n - 1
    while (len(prev_states_next)<n_steps):
        prev_states_next.insert(0,np.zeros(numBlocks*2))
        prev_states_current.insert(0,np.zeros(numBlocks*2))
    prev_states_next = np.array(prev_states_next)
    prev_states_current = np.array(prev_states_current)

    prev_states_current = np.reshape(prev_states_current,(-1,n_steps,n_input))
    prev_states_next = np.reshape(prev_states_next,(-1,n_steps,n_input))
    return prev_states_current,prev_states_next

def make_epsilon_greedy_policy(estimator, nA):
    """
    Creates an epsilon-greedy policy based on a given Q-function approximator and epsilon.

    Args:
        estimator: An estimator that returns q values for a given state
        nA: Number of actions in the environment.

    Returns:
        A function that takes the (sess, observation, epsilon) as an argument and returns
        the probabilities for each action in the form of a numpy array of length nA.

    """
    def policy_fn(sess, observation, epsilon,save_cell_states):
        A = np.ones(nA, dtype=float) * epsilon / nA
        #prev_states = computePreviousStates(replay_memory,n_steps,False)
#        q_values = estimator.predict(sess, np.expand_dims(observation, 0))[0]
        q_values = estimator.predict(sess, observation,save_cell_states)[0]
        print ("\nQ values")
        print (q_values)       
        best_action = np.argmax(q_values)
        A[best_action] += (1.0 - epsilon)
        #print (A)
        return A
    return policy_fn


# In[ ]:


def deep_q_learning(sess,
                    env,
                    q_estimator,
                    target_estimator,
                    state_processor,
                    num_episodes,
                    experiment_dir,
                    replay_memory_size=500000,
                    replay_memory_init_size=50000,
                    update_target_estimator_every=10000,
                    discount_factor=0.99,
                    epsilon_start=1.0,
                    epsilon_end=0.1,
                    epsilon_decay_steps=500000,
                    batch_size=32,
                    record_video_every=50):
    """
    Q-Learning algorithm for fff-policy TD control using Function Approximation.
    Finds the optimal greedy policy while following an epsilon-greedy policy.

    Args:
        sess: Tensorflow Session object
        env: OpenAI environment
        q_estimator: Estimator object used for the q values
        target_estimator: Estimator object used for the targets
        state_processor: A StateProcessor object
        num_episodes: Number of episodes to run for
        experiment_dir: Directory to save Tensorflow summaries in
        replay_memory_size: Size of the replay memory
        replay_memory_init_size: Number of random experiences to sampel when initializing 
          the reply memory.
        update_target_estimator_every: Copy parameters from the Q estimator to the 
          target estimator every N steps
        discount_factor: Lambda time discount factor
        epsilon_start: Chance to sample a random action when taking an action.
          Epsilon is decayed over time and this is the start value
        epsilon_end: The final minimum value of epsilon after decaying is done
        epsilon_decay_steps: Number of steps to decay epsilon over
        batch_size: Size of batches to sample from the replay memory
        record_video_every: Record a video every N episodes

    Returns:
        An EpisodeStats object with two numpy arrays for episode_lengths and episode_rewards.
    """
    print ('replay_memory_size = ' + str(replay_memory_size))
    print ('replay_memory_init_size = ' + str(replay_memory_init_size))
    print ('update_target_estimator_every = ' + str(update_target_estimator_every))
    print ('epsilon_decay_steps = ' + str(epsilon_decay_steps))
    print ('batch_size = ' + str(batch_size))
    print ('numBlocks = ' + str(numBlocks))
    print ('n_steps = ' + str(n_steps))
    print ('n_hidden = ' + str(n_hidden))
#    dict = {'replay_memory_size': str(replay_memory_size)}
    dict = {'replay_memory_size': replay_memory_size, 'replay_memory_init_size': replay_memory_init_size,
        'update_target_estimator_every':update_target_estimator_every,'epsilon_decay_steps':epsilon_decay_steps,
        'batch_size':batch_size,'numBlocks':numBlocks,n_steps:'n_steps',
        'n_hidden':n_hidden}
    file = open(experiment_dir + '/dump.txt', 'wb')
    pickle.dump(str.encode(str(dict)), file)
    file.close()


    #Transition = namedtuple("Transition", ["state", "action", "reward", "next_state", "done"])
    Transition = namedtuple("Transition", ["state", "action", "reward", "next_state", "done","prev_states"])
    # The replay memory
    replay_memory = []
    
    # Make model copier object
    estimator_copy = ModelParametersCopier(q_estimator, target_estimator)

    # Keeps track of useful statistics
    stats = plotting.EpisodeStats(
        episode_lengths=np.zeros(num_episodes),
        episode_rewards=np.zeros(num_episodes))
    
    # For 'system/' summaries, usefull to check if currrent process looks healthy
    current_process = psutil.Process()

    # Create directories for checkpoints and summaries
    checkpoint_dir = os.path.join(experiment_dir, "checkpoints")
    checkpoint_path = os.path.join(checkpoint_dir, "model")
    monitor_path = os.path.join(experiment_dir, "monitor")
    
    if not os.path.exists(checkpoint_dir):
        os.makedirs(checkpoint_dir)
    if not os.path.exists(monitor_path):
        os.makedirs(monitor_path)

    saver = tf.train.Saver()
    # Load a previous checkpoint if we find one
    latest_checkpoint = tf.train.latest_checkpoint(checkpoint_dir)
    if latest_checkpoint:
        print("Loading model checkpoint {}...\n".format(latest_checkpoint))
        saver.restore(sess, latest_checkpoint)
    
    # Get the current time step
    total_t = sess.run(tf.contrib.framework.get_global_step())

    # The epsilon decay schedule
    epsilons = np.linspace(epsilon_start, epsilon_end, epsilon_decay_steps)

    # The policy we're following
    policy = make_epsilon_greedy_policy(
        q_estimator,
        len(VALID_ACTIONS))

    # The following code will show all the trainable variables
    #variables_names = [v.name for v in tf.trainable_variables()]
    #values = sess.run(variables_names)
    #for k, v in zip(variables_names, values):
    #    print ("Variable: ", k)
    #    print ("Shape: ", v.shape)
    #    print (v)
    #input ('Press any key')
    
    # Populate the replay memory with initial experience
    print("Populating replay memory...")
    state = env.reset()
    state = state_processor.process(sess, state)
#    print (state.shape)
#    print (state)
#    state = np.stack([state] * 4, axis=2)    
    t= 0
    for i in range(replay_memory_init_size):
        #New version follows the existing policy, only useful if the estimator has been pre-loaded
        prev_states_current,prev_states_next = computePreviousStates(replay_memory,n_steps)
        action_probs = policy(sess, prev_states_current, epsilons[min(total_t, epsilon_decay_steps-1)],True)
        action = np.random.choice(np.arange(len(action_probs)), p=action_probs)
        #print ("\nTaking action " + str(VALID_ACTIONS[action]))        
        #Old version without following the policy
        #action = np.random.choice(VALID_ACTIONS)
        next_state, reward, done, _ = env.step(VALID_ACTIONS[action])
        next_state = state_processor.process(sess, next_state)
        #next_state = np.append(state[:,:,1:], np.expand_dims(next_state, 2), axis=2)
        prev_states_current,prev_states_next = computePreviousStates(replay_memory,n_steps)      
        # Incorrect code, delete
        #if (t!=0):
        #    print ('Inside loop t not zero')
        #    print (state)
        #    state[numBlocks:]=numBlocks+1
        #    print (state)
        # Move the following line inside the if
        #replay_memory.append(Transition(state, action, reward, next_state, done,prev_states_next))
        # Trick to consider the case where we end the episode, the next state will change
        # because we reset the environment
        orig_state = state
        if done:
            t=0
            state = env.reset()
            state = state_processor.process(sess, state)
            # Note that we use state as the next_state because we have reset the environment
            replay_memory.append(Transition(orig_state, action, reward, state, done,prev_states_next))
            #state = np.stack([state] * 4, axis=2)

        else:
            replay_memory.append(Transition(state, action, reward, next_state, done,prev_states_next))
            state = next_state
            t=t+1

    # Record videos
    # Add env Monitor wrapper
    #env = Monitor(env, directory=monitor_path, video_callable=lambda count: count % record_video_every == 0, resume=True)

    for i_episode in range(num_episodes):

        # Save the current checkpoint
        saver.save(tf.get_default_session(), checkpoint_path)

        # Reset the environment
        state = env.reset()
        env.render()
        state = state_processor.process(sess, state)
        #state = np.stack([state] * 4, axis=2)
        loss = None
        print ('\n******** NEW EPISODE ***********************************\n')
        # One step in the environment
        for t in itertools.count():

            # Epsilon for this time step
            epsilon = epsilons[min(total_t, epsilon_decay_steps-1)]

            # Maybe update the target estimator
            if total_t % update_target_estimator_every == 0:
                estimator_copy.make(sess)
                print("\nCopied model parameters to target network.")

            # Print out which step we're on, useful for debugging.
            print("\rStep {} ({}) @ Episode {}/{}, loss: {}".format(
                    t, total_t, i_episode + 1, num_episodes, loss), end="")
            sys.stdout.flush()

            # Take a step
            prev_states_current,prev_states_next = computePreviousStates(replay_memory,n_steps)

            #action_probs = policy(sess, state, epsilon)
            action_probs = policy(sess, prev_states_current, epsilon,True)
            action = np.random.choice(np.arange(len(action_probs)), p=action_probs)
            print ("\nTaking action " + str(VALID_ACTIONS[action]))
            next_state, reward, done, _ = env.step(VALID_ACTIONS[action])
            #env.render()
            next_state = state_processor.process(sess, next_state)
#            next_state = np.append(state[:,:,1:], np.expand_dims(next_state, 2), axis=2)

            # If our replay memory is full, pop the first element
            if len(replay_memory) == replay_memory_size:
                replay_memory.pop(0)

            # Save transition to replay memory
            # Here we should consider that when the episode is done next_state should be replaced
            # by the first state in the next episode?
            replay_memory.append(Transition(state, action, reward, next_state, done,prev_states_next))
            #replay_memory.append(Transition(state, action, reward, next_state, done))

            # Update statistics
            stats.episode_rewards[i_episode] += reward
            stats.episode_lengths[i_episode] = t

            # Sample a minibatch from the replay memory
            samples = random.sample(replay_memory, batch_size)
            states_batch, action_batch, reward_batch, next_states_batch, done_batch,prev_next_states_batch = map(np.array, zip(*samples))
            # Shape of next_states_batch is batch_size*n_input
            #print (next_states_batch.shape)
            #print (prev_next_states_batch.shape)
            # Calculate q values and targets
#            q_values_next = target_estimator.predict(sess, next_states_batch)
            #print (prev_next_states_batch[0])
            # Shape of prev_next_states_batch is batch_size*n_steps*n_output
            prev_next_states_batch = np.reshape(prev_next_states_batch,(-1,n_steps,numBlocks*2))
            #print (prev_next_states_batch[0])
            #print (prev_next_states_batch.shape)
            q_values_next = target_estimator.predict(sess, prev_next_states_batch,False)
            #print ('Q values next')
            #print (q_values_next.shape)
            #print (q_values_next)
            targets_batch = reward_batch + np.invert(done_batch).astype(np.float32) * discount_factor * np.amax(q_values_next, axis=1)
            #print ('Targets')
            #print (targets_batch.shape)            
            #print (targets_batch)           
            # Perform gradient descent update
            #states_batch = np.array(states_batch) 
            states_batch = np.array(prev_next_states_batch)
            loss = q_estimator.update(sess, states_batch, action_batch, targets_batch)

            if done:
                print ('PROBLEM SOLVED')
                print ('\n*******************************************\n')
                #We just make this call because we want to store the last state of the episode
                prev_states_current,prev_states_next = computePreviousStates(replay_memory,n_steps)
                action_probs = policy(sess, prev_states_current, epsilon,True)
                break
            #if (loss>500):
            #    break

            state = next_state
            total_t += 1

        # Add summaries to tensorboard
        episode_summary = tf.Summary()
        episode_summary.value.add(simple_value=epsilon, tag="episode/epsilon")
        episode_summary.value.add(simple_value=stats.episode_rewards[i_episode], tag="episode/reward")
        episode_summary.value.add(simple_value=stats.episode_lengths[i_episode], tag="episode/length")
        episode_summary.value.add(simple_value=current_process.cpu_percent(), tag="system/cpu_usage_percent")
        episode_summary.value.add(simple_value=current_process.memory_percent(memtype="vms"), tag="system/v_memeory_usage_percent")
        q_estimator.summary_writer.add_summary(episode_summary, i_episode)
        q_estimator.summary_writer.flush()
        
        yield total_t, plotting.EpisodeStats(
            episode_lengths=stats.episode_lengths[:i_episode+1],
            episode_rewards=stats.episode_rewards[:i_episode+1])

    return stats


# In[ ]:

env = gym.envs.make("BlocksWorld-v0")
    
tf.reset_default_graph()



# Where we save our checkpoints and graphs
#experiment_dir = os.path.abspath("./experiments/{}".format(env.spec.id))
# Check if one parameter was used as command line argument
#if (len(sys.argv)>1):
#    experiment_dir = sys.argv[1]
#else:
experiment_dir = os.path.abspath("./experiments/{}".format(filename))
#experiment_dir = ("/Users/rubengarzon/Documents/Projects/phD/Repo/LSTMs/experiments/BlocksWorld_170826_173039_c")

# Create a glboal step variable
global_step = tf.Variable(0, name='global_step', trainable=False)
    
# Create estimators
q_estimator = Estimator(scope="q_estimator", summaries_dir=experiment_dir)
target_estimator = Estimator(scope="target_q")

# State processor
state_processor = StateProcessor()

# Run it!
with tf.Session() as sess:
    sess.run(tf.global_variables_initializer())
    q_estimator.add_sessionGraph(sess)
    for t, stats in deep_q_learning(sess,
                                    env,
                                    q_estimator=q_estimator,
                                    target_estimator=target_estimator,
                                    state_processor=state_processor,
                                    experiment_dir=experiment_dir,
#                                    num_episodes=10000,
                                    num_episodes=300,
#                                    replay_memory_size=500000,
                                    replay_memory_size=1024,
#                                    replay_memory_init_size=50000,
                                    replay_memory_init_size=128,
#                                    update_target_estimator_every=10000,
                                    update_target_estimator_every=500,
                                    epsilon_start=1.0,
                                    epsilon_end=0.1,
#                                    epsilon_decay_steps=500000,
                                    epsilon_decay_steps=800,
                                    discount_factor=0.99,
#                                    batch_size=32):
                                    batch_size=32):
        print("\nEpisode Reward: {}".format(stats.episode_rewards[-1]))

q_estimator.save_states(experiment_dir + "/lstmvis")

ep_length,ep_reward,t_steps = plotting.plot_episode_stats (stats, smoothing_window=5,noshow=True)
ep_length.savefig(experiment_dir + '/ep_length.png')
ep_reward.savefig(experiment_dir + '/ep_reward.png')
t_steps.savefig(experiment_dir + '/t_steps.png')

if (os.path.exists("./log.txt")):
    copyfile("./log.txt", experiment_dir + "/log.txt")
    os.remove("./log.txt")
    

# In[ ]:




# In[ ]:



