#!/bin/sh
# waits for stage 2, then runs stages 3, 4, 5 (each resumes / skips finished work)
cd "$(dirname "$0")/../.."
L=reports/research_v3
until grep -q "\[s2_m5_total_r\] 3000/3000" $L/s2.log 2>/dev/null; do sleep 60; done
python -u research/v3/s3_timeframes.py 1500 > $L/s3.log 2>&1 &&
python -u research/v3/s4_mtf.py 1200 > $L/s4.log 2>&1 &&
python -u research/v3/s5_validate.py > $L/s5.log 2>&1
echo "pipeline exit $?" >> $L/pipeline.log
