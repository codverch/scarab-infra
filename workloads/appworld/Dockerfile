# syntax = edrevo/dockerfile-plus
FROM ubuntu:focal

INCLUDE+ ./common/Dockerfile.common

USER root

# AppWorld (StonyBrookNLP/appworld) requires Python 3.11+; Ubuntu 20.04's
# default python3 is 3.8. Build 3.11 from the official source tarball rather
# than the deadsnakes PPA: PPA installs need a launchpad keyserver round-trip
# for the signing key, which is unreliable from a sandboxed build network
# (observed: add-apt-repository silently added the source but apt never
# fetched its Packages index, only InRelease, so python3.11 was "not found").
# A source build only needs a plain HTTPS download from python.org.
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y \
    build-essential \
    zlib1g-dev \
    libncurses5-dev \
    libgdbm-dev \
    libnss3-dev \
    libssl-dev \
    libreadline-dev \
    libffi-dev \
    libsqlite3-dev \
    libbz2-dev \
    liblzma-dev \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR $tmpdir

RUN cd $tmpdir && \
    wget -nc https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tgz && \
    tar xzf Python-3.11.9.tgz && \
    cd Python-3.11.9 && \
    ./configure --prefix=$tmpdir/python3.11 --with-ensurepip=install && \
    make -j$(nproc) && \
    make install

RUN $tmpdir/python3.11/bin/python3.11 -m venv $tmpdir/appworld_venv && \
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
