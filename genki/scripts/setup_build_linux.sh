#!/bin/sh

set -ex

sudo apt-get update
sudo apt-get install -y apt-utils software-properties-common
sudo apt-get -y update
sudo apt-get -y upgrade
sudo apt-get -y install -q \
		openssh-client \
		wget \
		git \
		ninja-build \
		libncurses5-dev \
		python3 \
		python3-pip \
		vim \
		gcc-12 \
		g++-12 \
		zlib1g-dev \
		genromfs \
		cmake

wget -O - 'https://apt.llvm.org/llvm-snapshot.gpg.key' | sudo apt-key add
sudo apt-add-repository 'deb https://apt.llvm.org/lunar/ llvm-toolchain-lunar-17 main'
sudo apt-get update
sudo apt-get -y install -q clang-17 libclang-17-dev
sudo update-alternatives --install /usr/bin/clang++ clang++ /usr/bin/clang++-17 1000
sudo update-alternatives --install /usr/bin/clang clang /usr/bin/clang-17 1000
sudo update-alternatives --auto clang
sudo update-alternatives --auto clang++

sudo apt install \
	python3-pyelftools \
	python3-jinja2 \
	python3-yaml \
	python3-pyparsing \
	python3-brotli \
	python3-buildbot-worker --upgrade

# Download manually from https://bitbucket.org/example-org/buildbot_ci/src/master/dockerdev/vendorLinEnv_ubuntu2304/
sudo dpkg -i ~/Downloads/arm-gnu-toolchain-14.2.rel1-x86_64-arm-none-eabi.deb
sudo dpkg -i ~/Downloads/armeb-gnu-toolchain-14.2.rel1-x86_64-arm-none-eabi.deb

wget -qO- https://deb.nodesource.com/setup_18.x | sudo bash -
sudo apt-get update && sudo apt-get -y install -q nodejs

echo 'export LLVM_PATH=/usr/lib/llvm-17/' >> ~/.profile
