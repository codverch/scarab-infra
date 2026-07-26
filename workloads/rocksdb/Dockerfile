# syntax = edrevo/dockerfile-plus
FROM ubuntu:focal

INCLUDE+ ./common/Dockerfile.common

# TODO: Install required packages
USER root

# Pin to a release that only requires C++17 (main now requires C++20, which
# this image's GCC 9 toolchain does not support).
RUN git clone https://github.com/facebook/rocksdb.git && \
    cd rocksdb && git checkout v9.7.3

#Build rocksdb and dbbench
RUN apt update && apt upgrade -y
RUN DEBIAN_FRONTEND=noninteractive apt-get install -y \
    libgflags-dev \
    time \
    libsnappy-dev \
    zlib1g-dev \
    libbz2-dev \
    libzstd-dev
RUN cd rocksdb && make static_lib
RUN cd rocksdb && DEBUG_LEVEL=0 make db_bench
#RUN cd examples && make all

#execute bulkload benchmark to preload the rocksb database
RUN cd rocksdb && DB_DIR=/tmp/rocksdbtest-1000/ WAL_DIR=/tmp/rocksdbtest-1000/ NUM_KEYS=100000000 ./tools/benchmark.sh bulkload
RUN chmod a+rwx /tmp
RUN chmod a+w /tmp/rocksdbtest-1000/.
RUN chmod a+w /tmp/rocksdbtest-1000/*

# Switch to the dcuser user

# start your application
CMD ["/bin/bash"]