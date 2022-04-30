FROM nvidia/cudagl:11.4.2-devel-ubuntu20.04

ENV DEBIAN_FRONTEND noninteractive
ENV DEBCONF_NONINTERACTIVE_SEEN true

## preesed tzdata, update package index, upgrade packages and install needed software
RUN truncate -s0 /tmp/preseed.cfg; \
    echo "tzdata tzdata/Areas select Europe" >> /tmp/preseed.cfg; \
    echo "tzdata tzdata/Zones/Europe select Berlin" >> /tmp/preseed.cfg; \
    debconf-set-selections /tmp/preseed.cfg && \
    rm -f /etc/timezone /etc/localtime && \
    apt-get update && \
    apt-get install -y tzdata && \
    apt-get install -y \
        bash python3 python3-pip python-is-python3 fish git neovim libcairo2-dev pkg-config python3-dev libcudnn8 && \
    rm -rf /var/lib/apt/lists/*
RUN pip3 install tensorflow tensorflow-datasets six==1.15.0 absl-py==0.12.0 pyparsing==2.4.7
RUN pip install --upgrade "jax[cuda]" -f https://storage.googleapis.com/jax-releases/jax_releases.html
RUN pip3 install plotly wandb pillow attrdict matplotlib pycairo dm-haiku optax
RUN pip3 install msgpack-rpc-python i
RUN pip3 install airsim Pillow
RUN echo "root:docker" | chpasswd
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT /entrypoint.sh