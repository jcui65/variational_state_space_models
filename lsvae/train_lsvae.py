import os
import sys
if __name__=="__main__":
    sys.path.append(os.path.abspath(os.path.join(__file__, '..', '..')))
    sys.path.pop(0)

#from jax.config import config as jax_config
#jax_config.update("jax_enable_x64", True)

import jax
import wandb#where do we use it?
import haiku as hk
import attrdict
import jax.numpy as jnp
import datasets
import matplotlib.pyplot as plt
from vis.filter import plt_filter

from itertools import islice
from util import str2bool, environment_setup, vmap_rng, plot_mean_std, increase_contrast, gallery

from lsvae.trainer import LSVAETrainer#lsvae.trainer must be a python code
from distribution.normal import MultivariateNormal, ConcentrationNormal
import tensorflow_datasets as tfds
import numpy as np
import math

import argparse
import pickle


environment_setup()

parser = argparse.ArgumentParser('Train a vanilla VAE')
parser.add_argument('--dataset', default='pendulum_trajectory/64x64_lv')
parser.add_argument('--learning_rate', type=float, default=0.001)
parser.add_argument('--z_samples', type=int, default=1024)
parser.add_argument('--train_batch_size', type=int, default=128)
parser.add_argument('--test_batch_size', type=int, default=128)
parser.add_argument('--iterations', type=int, default=40000)
parser.add_argument('--fit_dynamics', type=str, choices=['True', 'False'], default='False')
parser.add_argument('--val_interval', type=int, default=100)
parser.add_argument('--save_interval', type=int, default=5000)
parser.add_argument('--sigma', type=float, default=None)
parser.add_argument('--seed', type=int, default=43)
parser.add_argument('--with_y', action="store_true")
parser.add_argument('--save', action="store_true")
parser.add_argument('--name', type=str, default=None)
parser.add_argument('--cont', type=str, default=None)

args = parser.parse_args()
args.fit_dynamics = args.fit_dynamics == 'True'

ds_config = {#it is a dictionary
    'pendulum_trajectory/64x64_lv': {
        'Sigma_w': jnp.array([[0.001, 0], [0, 0.001]]),
        'dec_sigma': 2,
        'init_L': 0.01,
        'min_cov': 0.001,
        'clip_factor': 5,
        'xlabel': r"$\theta$",
        'ylabel': r"$\dot{\theta}$"
    },
    'airsim_trajectory/zhang_jiajie_64x64': {
        'Sigma_w': 0.001*jnp.eye(4),
        'dec_sigma': 2,
        'init_L': 0.1,
        'min_cov': 0.005,
        'clip_factor': 0,
        'xlabel': 'x',
        'ylabel': 'y'
    },
    'airsim_trajectory/blocks_64x64': {
        'Sigma_w': 0.001*jnp.eye(4),
        'dec_sigma': 2,
        'init_L': 0.1,
        'min_cov': 0.005,
        'clip_factor': 0,
        'xlabel': 'x',
        'ylabel': 'y'
    }
}[args.dataset]#line 35
args.sigma = ds_config['dec_sigma'] if args.sigma is None else args.sigma
args.Sigma_w = ds_config['Sigma_w']
args.init_L = ds_config['init_L']#see page 7 of the paper
args.min_cov = ds_config['min_cov']#min_covariance?
args.clip_factor = ds_config['clip_factor']

wandb.init(config=args, project='lsvae', entity='dpfrom')
wandb.run.name = args.name
config = wandb.config

(train, test), info = tfds.load(config.dataset, split=("train", "test"), shuffle_files=False, with_info=True)
#through this you get the training and the testing data
train = train.repeat().shuffle(10*config.train_batch_size, seed=1231243).batch(config.train_batch_size)
#though do not know the details, I think it is the shuffling command
test = test.repeat().batch(config.test_batch_size)
#for test dataset, do you do shuffling?

# get more info about the dataset for configuring the algorithm
channels = info.features['images'].shape[-1]#for info, see around line 93
A, B, Prior, Sigma = info.metadata['A'], info.metadata['B'], info.metadata['Prior'], info.metadata['Sigma']
z_dim = Prior.shape[0]#for prior see around one line before
config.channels = channels#for channels, see  about 3 lines befor
config.z_dim = z_dim#2 lines before
config.A = A
config.B = B
config.Prior = Prior#prior covariance in page 6 of the paper?
config.Sigma = Sigma#noise covariance in page 6 of the paper?

from lsvae.model import build_lsvae#lsvae.trainer must be a python code

def model_extra(rng, batch, lsvae, res):
    image_model = lsvae.obs_models[0]
    decode_samples = vmap_rng(
        lambda rng, z: image_model.decode(z, False, []).mode['images']
    )
    # decode whole batch + time
    decode_batch = hk.BatchApply(decode_samples, 2)
    # decode the samples from the model
    # decode the 0th samples
    decoded = decode_batch(hk.next_rng_key(), res['data']['z'][:3, 0])#3 lines earlier

    qzx_qz = ConcentrationNormal(
        res['data']['qzx'].inf[0],
        res['data']['qzx'].conc[0] - lsvae.Prior.conc # divide by qz
    )
    prior = ConcentrationNormal(
        res['data']['prior'].inf[0],
        res['data']['prior'].conc[0]
    )
    post = ConcentrationNormal(
        res['data']['post'].inf[0],
        res['data']['post'].conc[0]
    )
    z, _ = lsvae._multi_sample(hk.next_rng_key(), post, batch['inputs'][0], 64)

    p_z = lsvae._prior_sample(hk.next_rng_key(), 10)#seems before entering decoder/generator
    p_z_decoded = decode_samples(hk.next_rng_key(), p_z)#seems after entering decoder/generator
    # sample random trajectories from the full prior
    return p_z, p_z_decoded, decoded, qzx_qz, prior, post, z, lsvae.A, lsvae.B # return only the first 4 decoded for the batch

def val_extra(batch, res, extra_res):
    p_z, p_z_decoded, x_reconst, qzx_qz, prior, post, z, A, B = extra_res#model extra function! it must be!
    images = batch['images'][:3]#3 channels images?
    # p_z is 10 x H x W x C, reorder to H x 10 x W x C
    t, h, w, c = p_z_decoded.shape#t is 10/ten, haha!
    p_z_decoded = np.transpose(p_z_decoded, (1, 0, 2, 3))#change the sequence!
    p_z_decoded = np.reshape(p_z_decoded, (h, t*w, c))#why do this?

    n, t, h, w, c = x_reconst.shape#x_reconst=decoded!
    # first reorder N X T X H X W X C to N X H X T x W x C
    x_reconst = np.transpose(x_reconst, (0, 2, 1, 3, 4))
    images = np.transpose(images, (0, 2, 1, 3, 4))
    # reshape to be N x H x T*W x C
    x_reconst = np.reshape(x_reconst, (n, h, w*t, c))#why do this?
    images = np.reshape(images, (n, h, w*t, c))
    # stack reconstructions, images along height
    together = np.concatenate((x_reconst, images), 1)#what is this?
    # reshape together to be N*H x 2*T*W x C
    displayed = np.reshape(together, (n*2*h, t*w, c))#why do this?

    means, stds, mu, sigma = plot_mean_std(res['data']['z'], 'z')#just a function for plotting?
    #what is the difference between means and mu?
    fig = plt.figure()
    # sample a bunch more using posteriors
    plt_filter(qzx_qz, prior, post, z, batch['states'][0],#plt_filter is a built-in function from a package
            ['', None], xlabel=ds_config['xlabel'], ylabel=ds_config['ylabel'],
            color=False)

    fig_color = plt.figure()
    plt_filter(qzx_qz, prior, post, z, batch['states'][0],
            ['', None], xlabel=ds_config['xlabel'], ylabel=ds_config['ylabel'],
            color=True)

    params_table = wandb.Table(columns=['param', 'value'])
    params_table.add_data('A', str(A))
    params_table.add_data('B', str(B))

    extra = {#it is another dictionary
        'reconst': wandb.Image(displayed),
        'samples': wandb.Image(np.array(p_z_decoded)),
        'means': means,
        'stds': stds,
        'filter': wandb.Image(fig),
        'filter_color': wandb.Image(fig_color),
        'params': params_table,
        'mu_0': mu[0],
        'mu_1': mu[1],
        'std_0': sigma[0],
        'std_1': sigma[1]
    }
    plt.close(fig)
    plt.close(fig_color)
    return extra

trainer = LSVAETrainer(lambda i: build_lsvae(config, i))
trainer.train(config, #wandb config
    iter(tfds.as_numpy(test)), iter(tfds.as_numpy(train)), #what is iter?
    val_extra, model_extra,#the numpyzation of train and test dataset
    seed=jax.random.PRNGKey(config.seed),#you know what it means, right?
    load=config.cont#what is .cont?
)
