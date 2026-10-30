cd /home/user/wt/pf
/home/user/security-testtry/.venv/bin/python scripts/ml/bench_matrix.py --bundle pf=/home/user/models/pf_int8_v2 --sample 1000 --ml-train-docs 1000 --cache-ml --out /home/user/results/pf_int8_matrix.json > /home/user/results/pf_int8_matrix.log 2>&1
/home/user/security-testtry/.venv/bin/python scripts/ml/bench_matrix.py --bundle gliner=/home/user/models/gliner_fp32_v2 --sample 1000 --ml-train-docs 1000 --cache-ml --only "gliner alone,current+gliner,current+spacy+gliner" --out /home/user/results/gliner_fp32_matrix.json > /home/user/results/gliner_fp32_matrix.log 2>&1
/home/user/security-testtry/.venv/bin/python scripts/ml/bench_matrix.py --bundle indicner=/home/user/models/indicner_int8 --min-score 0.3 --sample 1000 --ml-train-docs 1000 --cache-ml --only "indicner alone,current+indicner,current+spacy+indicner" --out /home/user/results/indicner_int8_matrix.json > /home/user/results/indicner_matrix.log 2>&1
echo ALLDONE > /home/user/results/alldone
