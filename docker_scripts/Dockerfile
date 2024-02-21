#FROM ubuntu:18.04
FROM nvidia/cuda:11.1.1-devel-ubuntu18.04

RUN apt-get -y update
RUN apt-get -y install wget unzip 

RUN apt-get -y update
RUN apt-get -y install git cmake g++ make

RUN apt-get -y update
RUN apt-get -y install libgl1-mesa-dev libgl1-mesa-glx libosmesa6-dev patchelf libopenmpi-dev libglew-dev libglfw3 libglfw3-dev

# for display through vnc
RUN apt-get -y update
RUN DEBIAN_FRONTEND=noninteractive apt-get install -y xfce4 tigervnc-standalone-server

# get python 3.8
RUN apt-get -y install python3.8 python3.8-dev python3.8-distutils
RUN ln -sv /usr/bin/python3.8 /usr/bin/python
RUN ln -svf /usr/bin/python3.8 /usr/bin/python3
RUN alias python3=python3.8
RUN wget https://bootstrap.pypa.io/get-pip.py && \
	python get-pip.py && python3 get-pip.py && \
	rm get-pip.py

RUN apt-get update
RUN apt-get -y install curl
RUN apt-get -y install python3.8-venv
RUN apt-get -y install ipython

# drake
RUN apt-get update
RUN curl -L -o drake.tar.gz https://github.com/RobotLocomotion/drake/releases/download/v0.33.0/drake-20210811-bionic.tar.gz

# python venv setup with drake
RUN mkdir -p /python_venvs/tamp
RUN tar -xvzf drake.tar.gz -C /python_venvs/tamp --strip-components=1
RUN yes | /python_venvs/tamp/share/drake/setup/install_prereqs
WORKDIR /python_venvs/
RUN python3 -m venv tamp --system-site-packages
WORKDIR /python_venvs/tamp

# ompl
RUN wget https://ompl.kavrakilab.org/install-ompl-ubuntu.sh -O /install-ompl-ubuntu.sh
RUN bash /install-ompl-ubuntu.sh --python

# utilities 
RUN apt-get update
RUN apt-get -y install firefox
RUN apt-get -y install vim
RUN apt-get -y install tmux
RUN apt-get -y install openssh-server
RUN apt-get -y install sudo
RUN apt-get -y install xfce4-terminal
RUN apt-get -y install gedit
RUN apt-get -y install trash-cli
RUN apt-get -y install ffmpeg
RUN apt-get -y install xdg-utils
RUN apt-get -y install liboctomap-dev libfcl-dev

RUN apt-get -y install apt-transport-https gnupg
RUN curl -fsSL https://bazel.build/bazel-release.pub.gpg | gpg --dearmor > bazel.gpg
RUN mv bazel.gpg /etc/apt/trusted.gpg.d/ 
RUN echo "deb [arch=amd64] https://storage.googleapis.com/bazel-apt stable jdk1.8" | sudo tee /etc/apt/sources.list.d/bazel.list
RUN apt-get update
RUN apt-get -y install bazel
RUN apt-get -y install jq

# scipy deps
RUN apt-get update && apt-get install -y \
    libblas-dev \
    liblapack-dev \
    gfortran

# cuda deps
RUN apt-get update && apt-get install -y \
    build-essential \
    libstdc++-6-dev

# pddlstream
#RUN git clone --recurse-submodules https://github.com/caelan/pddlstream.git pddlstream
#RUN cd pddlstream && ./FastDownward/build.py release64
#RUN cd pddlstream/FastDownward/builds && ln -s release64 release32
#ENV PYTHONPATH=${PYTHONPATH}:/python_venvs/tamp/pddlstream

# update terminal config
RUN printf "2\n" | update-alternatives --config x-terminal-emulator

# arguments when building image
# login password 
ARG password 
# $UID
ARG id
# $USER
ARG user
ENV PASS=$password

RUN groupadd -g $id $user
RUN useradd -s /bin/bash -u $id -g $id -m $user && echo "$user:$password" | chpasswd && adduser $user sudo
RUN chown -R $user /python_venvs 
ENV PATH="/python_venvs/tamp/bin:$PATH"
USER $user

WORKDIR /home/$user/
# mujoco
RUN wget https://www.roboti.us/download/mujoco200_linux.zip -O mujoco.zip
RUN unzip mujoco.zip -d .mujoco
RUN mv .mujoco/mujoco200_linux .mujoco/mujoco200
# add mujoco to LD_LIBRARY_PATH
ENV LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/home/$user/.mujoco/mujoco200/bin

WORKDIR /home/$user/
# start ikea specific stuff #
RUN git clone https://github.com/allenapplehead/furniture.git
WORKDIR /home/$user/furniture
RUN git fetch origin aug2020_fixes:aug2020_fixes
RUN git checkout aug2020_fixes
RUN ln -s /home/$user/.mujoco/mujoco200 /home/$user/.mujoco/mujoco200_linux
#RUN pip install --upgrade pip
RUN pip3 install scikit-build
RUN pip3 install --upgrade pip setuptools wheel
RUN pip3 install glfw>=1.4.0 numpy>=1.19.5 Cython>=0.27.2 imageio>=2.1.2 cffi>=1.10 fasteners~=0.15 lockfile
# copy mujoco license key `mjkey.txt` 
COPY mjkey.txt /home/$user/.mujoco/mjkey.txt
RUN pip3 install -r requirements.txt

WORKDIR /home/$user/
# other requirements
COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt
RUN pip3 install torch==1.9.0+cu111 -f https://download.pytorch.org/whl/torch_stable.html

ENV CUDA_HOME=/usr/local/cuda
RUN pip install torch-scatter==2.0.9 -f https://data.pyg.org/whl/torch-1.9.0+cu111.html
RUN pip install torch-sparse torch-cluster torch-spline-conv torch-geometric -f https://pytorch-geometric.com/whl/torch-1.9.0+cu111.html
ENV PYTHONPATH=${PYTHONPATH}:/home/$user/drake-tamp/:/home/$user/drake-tamp/pddlstream/:/home/$user/furniture/:/home/$user/drake-tamp/experiments/ikea_induction/

RUN echo "source /python_venvs/tamp/bin/activate" >> ~/.bashrc
RUN echo "export  USER=$user" >> ~/.bashrc
RUN echo "export  PYTHONPATH=${PYTHONPATH}:/home/$user/drake-tamp/:/home/$user/drake-tamp/pddlstream/:/home/$user/experiments/ikea_induction/:/home/$user/drake-tamp/pddlstream/examples/pybullet/utils/" >> ~/.bashrc
RUN echo "export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/home/$user/.mujoco/mujoco200/bin" >> ~/.bashrc

ARG gitname 
ARG gitemail 

RUN git config --global user.name $gitname
RUN git config --global user.email $gitemail

WORKDIR /home/$user/drake-tamp
COPY .tmux.conf /home/$user/.tmux.conf
COPY .vimrc /home/$user/.vimrc

COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["bash","/entrypoint.sh"]

CMD ["bash"]
