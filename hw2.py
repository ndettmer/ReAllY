import numpy as np
import ray
from really import SampleManager
import tensorflow as tf
from tensorflow.keras.layers import Dense, LeakyReLU
import gym
import os
from datetime import datetime

from really.utils import dict_to_dict_of_datasets


class DQN(tf.keras.Model):
    def __init__(self, action_space=2, input_shape=2):
        super().__init__()
        self.action_space = action_space
        self.d1 = Dense(16, activation=LeakyReLU())
        self.d2 = Dense(32, activation=LeakyReLU())
        self.d3 = Dense(32, activation=LeakyReLU())
        self.dout = Dense(self.action_space, activation=None)

    def call(self, state):
        output = {}
        hidden = self.d1(state)
        hidden = self.d2(hidden)
        hidden = self.d3(hidden)
        q = self.dout(hidden)
        output["q_values"] = q
        return output


if __name__ == "__main__":

    tf.keras.backend.set_floatx('float64')

    # initialize
    ray.init(log_to_driver=False)
    manager = SampleManager(DQN, 'CartPole-v0',
                            num_parallel=3, total_steps=100,
                            action_sampling_type="thompson")

    buffer_size = 2000
    epochs = 100
    saving_path = os.getcwd() + "/progress_dqn"
    saving_after = 5
    sample_size = 100
    optim_batch_size = 8
    gamma = .98
    update_interval = 4
    test_steps = 1000
    temperature = 1.5
    temperature_update = 0.98 #new_temp = old_temp*temp_update
    temperature_min = 0.5

    # keys for replay buffer -> what you will need for optimization
    optim_keys = ["state", "action", "reward", "state_new", "not_done"]

    # initialize buffer
    manager.initilize_buffer(buffer_size, optim_keys)

    manager.initialize_aggregator(
        path=saving_path,
        saving_after=saving_after,
        aggregator_keys=['loss', 'time_steps']
    )

    # get initial agent
    manager.set_temperature(temperature)
    agent = manager.get_agent()


    while True:
        # Check if buffer is already filled
        if len(manager.buffer.buffer[manager.buffer.keys[0]]) >= manager.buffer.size:
            break
        print("Filling buffer before training..")

        # Gives you state action reward trajetories
        data = manager.get_data()
        manager.store_in_buffer(data)

    optimizer = tf.keras.optimizers.Adam()

    for e in range(epochs):
        if e % update_interval == 0:
            target_agent = manager.get_agent()
        print("collecting experience..")

        # Gives you state action reward trajetories
        data = manager.get_data()
        manager.store_in_buffer(data)

        sample_dict = manager.sample(sample_size)
        print(f"collected data for: {sample_dict.keys()}")
        data_dict = dict_to_dict_of_datasets(sample_dict, batch_size=optim_batch_size)

        print("optimizing... ")
        total_loss = []
        for state, action, reward, state_new, not_done in \
            zip(data_dict['state'],
                data_dict['action'],
                data_dict['reward'],
                data_dict['state_new'],
                data_dict['not_done']):


            not_done = tf.cast(not_done, tf.bool)
            q_new = tf.where(not_done, target_agent.max_q(state_new), 0)

            # Don't put the target into GradientTape context
            q_target = reward + gamma * q_new
            with tf.GradientTape() as tape:
                q_output = agent.q_val(state, action)
                loss = tf.keras.losses.MSE(q_target, q_output)

            total_loss.append(loss)
            gradients = tape.gradient(loss, agent.model.trainable_variables)
            optimizer.apply_gradients(zip(gradients, agent.model.trainable_variables))

        # Update the agent
        manager.set_agent(agent.get_weights())
        agent = manager.get_agent()

        # Update temperature
        if temperature > temperature_min:
            temperature = temperature*temperature_update
            manager.set_temperature(temperature)

        # Update aggregator
        time_steps = manager.test(test_steps)
        manager.update_aggregator(loss=total_loss, time_steps=time_steps)
        print(
            f"epoch ::: {e}  loss ::: {np.mean(total_loss)}   avg env steps ::: {np.mean(time_steps)}   temperature ::: {temperature}"
        )

        if e % saving_after == 0:
            # you can save models
            manager.save_model(saving_path, e)


# and load models
    manager.load_model(saving_path)
    print("done")
    print("testing optimized agent")
    manager.test(test_steps, test_episodes=4, render=True)