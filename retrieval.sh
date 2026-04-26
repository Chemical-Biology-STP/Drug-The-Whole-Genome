#!/bin/bash
# DrugCLIP virtual screening script.
#
# Molecule embeddings are cached automatically based on the LMDB filename.
# First run encodes and saves; subsequent runs load from cache.
# To override the cache location, add: --cache-dir /path/to/cache


echo "First argument: $1"

MOL_PATH="data/enamine_dds10.lmdb"
POCKET_PATH="./data/targets/6QTP/pocket.lmdb"
FOLD_VERSION=6_folds
save_path="results_6QTP.txt"




python ./unimol/retrieval.py --user-dir ./unimol $data_path "./dict" --valid-subset test \
       --num-workers 8 --ddp-backend=c10d --batch-size 4 \
       --task drugclip --loss in_batch_softmax --arch drugclip  \
       --max-pocket-atoms 511 \
       --fp16 --fp16-init-scale 4 --fp16-scale-window 256  --seed 1 \
       --log-interval 100 --log-format simple \
       --mol-path $MOL_PATH \
       --pocket-path $POCKET_PATH \
       --fold-version $FOLD_VERSION \
       --use-cache False \
       --save-path $save_path
