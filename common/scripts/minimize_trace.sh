BINDIR=$1
TRACEFILE=$2
SPDIR=$3

# SEGSIZE is assumed to be the same as the chunk
# (simpoints are assumed to be the same size as the chunk)

# warmup is specified in the unit of chunks, e.g., 1 or 4
WARMUPCHUNKSORG=$4
# supposed to be /home/$USER/simpoint_flow/APPNAME/simp_traces
OUTDIR=$5

# create the folders
# a single copy of bin
# unzip the first chunk + the warmup chunks + the simpoint chunk -> a segment folder

cd $OUTDIR

# create the main directory structure
mkdir -p bin raw trace

# read in simpoints
declare -A clusterMap
while IFS=" " read -r segID clusterID; do
clusterMap[$clusterID]=$segID
done < $SPDIR/opt.p.lpt0.99

# even if zero is included in the simulation region,
# copy chunk zero to get rid of the special case handling and embrace laziness <- does not work
# zip will not append the same file to the same archive
# and dynamirio does not like to read chunk 0 header twice as well
# need the special case handling
echo "unzipping chunk 0000"
unzip "$TRACEFILE" "chunk.0000" -d "."

# create simp trace folder and unzip original trace
for clusterID in "${!clusterMap[@]}"
do
    WARMUP=$WARMUPCHUNKSORG
    segID=${clusterMap[$clusterID]}

    # unzip /path/to/archive.zip "in/archive/folder/*" -d "/path/to/unzip/to"
    # ref: https://unix.stackexchange.com/questions/59276/how-to-extract-only-a-specific-folder-from-a-zipped-archive-to-a-given-directory

    # the simulation region, in the unit of chunks
    roiStart=$segID
    # seq is inclusive
    roiEnd=$segID

    if [ "$roiStart" -gt "$WARMUP" ]; then
        # enough room for warmup, extend roi start to the left
        roiStart=$(( $roiStart - $WARMUP ))
    else
        # no enough preceding instructions, can only warmup till segment start
        WARMUP=$roiStart
        # new roi start is the very first instruction of the trace
        roiStart=0
    fi

    # create temporary directory for this segment
    tempDir="temp_$segID"
    mkdir -p "$tempDir"

    # copy chunk zero
    echo "copying chunk 0000"
    cp ./chunk.0000 "$tempDir/"

    # append to zip file
    zip -j -m "./$segID.zip" "$tempDir/chunk.0000"

    for chunkID in $(seq $roiStart $roiEnd);
    do
        # ref: https://stackoverflow.com/questions/1117134/padding-zeros-in-a-string
        padChunkID=$(printf %04d $chunkID)
        echo "unzipping chunk $padChunkID"
        unzip "$TRACEFILE" "chunk.$padChunkID" -d "$tempDir"
        # append to zip file
        zip -j -m "./$segID.zip" "$tempDir/chunk.$padChunkID"
    done

    # clean up temp directory
    rm -rf "$tempDir"

    # copy bin directory and modules.log to main directories (only once)
    if [ ! -d "bin" ] || [ -z "$(ls -A bin)" ]; then
        echo "copying bin directory and modules.log"
        cp -r "$BINDIR"/* bin/
        if [ -f "$BINDIR/../raw/modules.log" ]; then
            cp "$BINDIR/../raw/modules.log" raw/
        fi
    fi
    
    # move the zip file to the main trace directory
    mv "$segID.zip" trace/
done

rm "./chunk.0000"
# after doing this, can move around the entire folder
# then run update modules log,
# and copy bin modules log into raw folder since
# sometimes people just assume modules log is also there
exit
