# syntax = edrevo/dockerfile-plus
FROM ubuntu:focal

INCLUDE+ ./common/Dockerfile.common

USER root

# ClickHouse standalone binary, used via `clickhouse local` so no server
# process/networking is needed for tracing. builds.clickhouse.com only
# serves a "master" (latest tested build) or numbered-version path, not a
# literal "stable" alias.
RUN cd $tmpdir && \
    wget -nc -O clickhouse https://builds.clickhouse.com/master/amd64/clickhouse && \
    chmod +x $tmpdir/clickhouse && \
    $tmpdir/clickhouse --version

# tpch-kit dbgen: the same TPC-H data generator used by the postgres TPC-H
# pipeline in this repo (scripts/bootstrap_postgres_tpch.sh), pinned by commit.
RUN cd $tmpdir && \
    git clone https://github.com/gregrahn/tpch-kit.git && \
    cd tpch-kit && git checkout --detach 852ad0a5ee31ebefeed884cea4188781dd9613a3 && \
    cd dbgen && make MACHINE=LINUX DATABASE=POSTGRESQL

# Generate TPC-H SF1 and load it into a persistent clickhouse-local data
# directory at image-build time, so tracing only captures query execution.
COPY workloads/clickhouse/init.sql $tmpdir/init.sql
RUN cd $tmpdir/tpch-kit/dbgen && ./dbgen -s 1 -f && \
    mkdir -p $tmpdir/ch_data && \
    $tmpdir/clickhouse local --path $tmpdir/ch_data --queries-file $tmpdir/init.sql && \
    for t in nation region part supplier partsupp customer orders lineitem; do \
        $tmpdir/clickhouse local --path $tmpdir/ch_data --format_csv_delimiter='|' \
            --query "INSERT INTO $t FROM INFILE '$tmpdir/tpch-kit/dbgen/$t.tbl' FORMAT CSV"; \
    done && \
    rm -f $tmpdir/tpch-kit/dbgen/*.tbl

COPY workloads/clickhouse/workload.sql $tmpdir/ch_workload.sql
RUN chmod -R a+rwX $tmpdir/ch_data $tmpdir/ch_workload.sql

CMD ["/bin/bash"]
