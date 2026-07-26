# syntax = edrevo/dockerfile-plus
FROM ubuntu:focal

INCLUDE+ ./common/Dockerfile.common

USER root

# AppWorld (StonyBrookNLP/appworld) requires Python 3.11+; Ubuntu 20.04's
# default python3 is 3.8, so install 3.11 from deadsnakes alongside it
# without disturbing the python3/python2 toolchain Dockerfile.common already
# set up for scarab-infra's own scripts.
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y \
    software-properties-common \
 && add-apt-repository -y ppa:deadsnakes/ppa \
 && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR $tmpdir

RUN python3.11 -m venv $tmpdir/appworld_venv && \
    $tmpdir/appworld_venv/bin/pip install --upgrade pip && \
    $tmpdir/appworld_venv/bin/pip install appworld==0.1.3.post1

# AppWorld caches its unpacked test/app code and reads its root (task data,
# experiment outputs) from these two locations. Pin both under $tmpdir
# (baked into the image) rather than letting them default to $HOME/.appworld
# and cwd: at trace/run time, /home/$user is bind-mounted fresh from the
# host's root_dir (see scarab-infra's docker run in scripts/utilities.py),
# so anything installed under $HOME during image build would not survive
# into the running container.
ENV APPWORLD_CACHE=$tmpdir/appworld_cache
ENV APPWORLD_ROOT=$tmpdir/appworld_root

# Unpack AppWorld's app/task code and bake the task data (API docs, task
# definitions, base DBs) at image-build time, so that tracing later only
# captures task-solving execution, not download/unpack of the benchmark data.
RUN cd $tmpdir && \
    mkdir -p $APPWORLD_ROOT && \
    $tmpdir/appworld_venv/bin/appworld install && \
    $tmpdir/appworld_venv/bin/appworld download data --root $APPWORLD_ROOT

COPY workloads/appworld/run_appworld_tasks.py $APPWORLD_ROOT/run_appworld_tasks.py
RUN chmod a+rwx -R $tmpdir/appworld_root $tmpdir/appworld_cache $tmpdir/appworld_venv

# Start your application
CMD ["/bin/bash"]
