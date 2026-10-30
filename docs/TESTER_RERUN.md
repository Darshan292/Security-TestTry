# Tester rerun: privacy gateway with an open ML detector

This runbook reruns your private test sets against the gateway with one open ML detector enabled, in one of two
configurations:

- **A.** current stack (spaCy `en_core_web_lg` alongside, legacy heuristics on, as in `registry/deployment.yaml`) plus
  `openai/privacy-filter` q4 (token classifier, Viterbi with default biases).
- **B.** current stack plus `urchade/gliner_multi_pii-v1` fp32 (zero-shot span model).

Nothing here is a release number. The dev bench that these pins come from is in
[ML_EVALUATION.md](ML_EVALUATION.md) ("Dev bench of open detectors"); the release gate and its protocol (section 3 and 4 there)
still apply to your sets. Do not tune `min_score_by_type`, the Viterbi biases or the thresholds on your sets.

What was verified on the VM that wrote this file is listed in section 14 (verification record). In short: both bundle
digests reproduce from the pinned revisions; both configurations start in production mode and answer the attested smoke
ticket with HTTP 200 and all checks passing; the placeholders that left the gateway were captured for A. Aliases such as
`[PERSON_09JGMZ]` change on every run.

## 0. Environment

Shell variables used by every block (set them once; nothing here is secret):

```bash
export RUN=${RUN:-$HOME/rerun}            # scratch root, outside the repository
export WT=${WT:-/home/user/wt/runbook}    # your checkout at the runbook's commit (or later)
export MODELS=${MODELS:-/home/user/models}
export PY=${PY:-/home/user/security-testtry/.venv/bin/python}     # gateway + runtime venv (see INSTALL.md)
export EXPORT_PY=${EXPORT_PY:-/home/user/venv-export/bin/python} # torch CPU + gliner 0.2.29 (export only)
export HF_HUB_DISABLE_XET=1               # the xet transfer failed with a CAS error here; plain HTTPS works
export PGW_PORT=${PGW_PORT:-8081}         # the gateway listens here; change it if the port is taken
mkdir -p "$RUN/keys" "$RUN/src" "$MODELS" && chmod 700 "$RUN/keys"
```

- The runtime venv needs `requirements/runtime.txt`, `requirements/ml.txt`, `requirements/ner.txt` and
  `requirements/ner-model.txt` (spaCy `en_core_web_lg` 3.8.0 is used alongside in both configurations). Install them with
  `--require-hashes` as in INSTALL.md.
- The export venv needs `gliner==0.2.29`, `torch 2.15.0+cpu`, `transformers 5.16.1`, `onnx`. Only used in Part 3.
- Every repository used here is public. You do not need `HF_TOKEN`. If you set one for rate limits, do not echo it.
- **Disk.** Have about 3.5 GB free before Part 2 (the peak is the GLiNER export plus the first bundle plus the second bundle
  copy). Each block below removes its intermediate copies as soon as they are no longer needed, so the peak stays there.
  Both bundles stay on disk (about 2.1 GB). A failed build leaves a partial output directory: remove it before retrying
  (`rm -rf "$MODELS/rerun_A"`; the build script refuses an existing directory).
- **RAM.** Each gateway worker holds its own model copy (see section 8). Plan for the spaCy workers as well:
  `ner_workers: 4` in the current profile.

## 1. Pins

| config | bundle | source @ revision | manifest digest (`python -m pgw.ml.artifact`) |
|---|---|---|---|
| A | `$MODELS/rerun_A` (privacy-filter q4, token) | openai/privacy-filter @ `7ffa9a043d54d1be65afb281eddf0ffbe629385b` | `cbabf1f661ea8180a48690ae4c341a38f2ea260b0ebae88ef55770258fe46b28` |
| B | `$MODELS/rerun_B` (GLiNER multi PII v1, fp32, span) | urchade/gliner_multi_pii-v1 @ `1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d`, encoder tokenizer microsoft/mdeberta-v3-base @ `a0484667b22365f84929a935b5e50a51f71f159d` | `1222c2e7e822bd9d3a569780e804798b3f6434863e9e24819ac26276ad4bd1c3` |

The digests are the doc's pins (ML_EVALUATION.md, bundles table). Both were reproduced here from the pinned revisions,
byte for byte (every file of each bundle equals the existing bench bundle, and `MODEL_CARD.md` matches too).

The label maps below are the reviewed maps of the bundles. Two points for the profile:

- **privacy-filter: `account_number: IGNORE` in the profile**, not the bundle's `BANK_ACCOUNT`. The bundle's `train_meta.json`
  (inside the digest) keeps `BANK_ACCOUNT`; the gateway does not read `train_meta.json`, so the profile can override it and the
  pinned digest stays valid. Mapping it to `BANK_ACCOUNT` breaks the union (ML_EVALUATION.md, "For whoever reruns").
- The profile `labels` must name every label of the model (`pgw/ml/config.py`); the gateway refuses startup otherwise.

## 2. Part 1: fetch the pinned sources and check every file

```bash
cd "$RUN/src"
"$PY" - <<'EOF'
import os, shutil
from huggingface_hub import snapshot_download
PF = "7ffa9a043d54d1be65afb281eddf0ffbe629385b"
p = snapshot_download(repo_id="openai/privacy-filter", revision=PF,
                      allow_patterns=["onnx/model_q4.onnx", "onnx/model_q4.onnx_data*", "tokenizer.json",
                                      "config.json", "viterbi_calibration.json"])
# repack_onnx.py reads plain regular files from one directory (no symlinks), so copy the real bytes out of the cache
dst = os.path.join(os.environ["RUN"], "src", "pfq4")
os.makedirs(dst, exist_ok=True)
for rel, name in [("onnx/model_q4.onnx", "model_q4.onnx"), ("onnx/model_q4.onnx_data", "model_q4.onnx_data"),
                  ("tokenizer.json", "tokenizer.json"), ("config.json", "config.json"),
                  ("viterbi_calibration.json", "viterbi_calibration.json")]:
    shutil.copyfile(os.path.join(p, rel), os.path.join(dst, name))
print("pf copied to", dst)
# the cache keeps a second copy of the same bytes; the bundle does not need it
shutil.rmtree(os.path.dirname(os.path.dirname(p)), ignore_errors=True)
EOF
cd "$RUN/src/pfq4" && sha256sum -c <<'EOF'
8f7dee8b46d096f052b359375dfba5d983cc4d18c44a783bf548615c472f8dea  model_q4.onnx
f30998e28c71c5374cc7e8b7de8f0f83e981592c0c2d652d2ad4928454dbb496  model_q4.onnx_data
0614fe83cadab421296e664e1f48f4261fa8fef6e03e63bb75c20f38e37d07d3  tokenizer.json
b2b26a4a4a000639ad30b0c264adbefe365bdb567fbd7bb27303b8c438375bd1  config.json
bbc8611ef08a55ed72d64856cbbbb9a91db8dfa881f0a92e2afbad6e4bbc775a  viterbi_calibration.json
EOF
```

GLiNER (the export reads the cache by repo and revision):

```bash
cd "$RUN/src"
"$PY" - <<'EOF'
from huggingface_hub import snapshot_download
snapshot_download(repo_id="urchade/gliner_multi_pii-v1", revision="1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d")
snapshot_download(repo_id="microsoft/mdeberta-v3-base", revision="a0484667b22365f84929a935b5e50a51f71f159d",
                  allow_patterns=["tokenizer*", "spm.model", "config.json", "special_tokens_map.json", "added_tokens.json"])
print("gliner and mdeberta cached")
EOF
"$PY" - <<'EOF'
import hashlib, os
from huggingface_hub import snapshot_download
p = snapshot_download(repo_id="urchade/gliner_multi_pii-v1", revision="1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d",
                      local_files_only=True)
want = {"pytorch_model.bin": "3003753fba99e40645cf088c7367a2c6211fc174897dc64f1f9c147c29d18d2d",
        "gliner_config.json": "2088bee85f50849dea2ab4cf411ab5cf3aaaf7c8a45c23412035978b2bc02801"}
for name, sha in want.items():
    got = hashlib.sha256(open(os.path.join(p, name), "rb").read()).hexdigest()
    assert got == sha, (name, got)
print("gliner source hashes ok")
EOF
```

## 3. Part 2: configuration A, bundle `rerun_A` (privacy-filter q4)

Repack the two external-data files into one inline `model.onnx`. The script pins both inputs by sha256 before parsing,
checks parity against onnxruntime's own load of the source, and refuses to overwrite:

```bash
cd "$WT"
"$PY" scripts/ml/repack_onnx.py "$RUN/src/pfq4" model_q4.onnx "$RUN/src/pfq4/model.onnx" \
  --expect model_q4.onnx=8f7dee8b46d096f052b359375dfba5d983cc4d18c44a783bf548615c472f8dea \
  --expect model_q4.onnx_data=f30998e28c71c5374cc7e8b7de8f0f83e981592c0c2d652d2ad4928454dbb496 --parity
# expected: parity max abs diff: 0.000e+00 and sha256 00f41770f92f275b9bd056ce434763856ce3191ce5eaaa4c81b8ab29908d054d
rm -f "$RUN/src/pfq4/model_q4.onnx" "$RUN/src/pfq4/model_q4.onnx_data"   # the repacked model.onnx is what gets built
```

The export note is copied from the existing bench card, so the card (which is inside the digest) reproduces:

```bash
cat > "$RUN/src/export_pf_q4.txt" <<'EOF'
Official ONNX from openai/privacy-filter@7ffa9a043d54d1be65afb281eddf0ffbe629385b, repacked to one inline file with scripts/ml/repack_onnx.py (parity max abs diff 0). Source files (sha256): onnx/model_q4.onnx 8f7dee8b46d096f052b359375dfba5d983cc4d18c44a783bf548615c472f8dea, onnx/model_q4.onnx_data f30998e28c71c5374cc7e8b7de8f0f83e981592c0c2d652d2ad4928454dbb496; tokenizer.json 0614fe83cadab421296e664e1f48f4261fa8fef6e03e63bb75c20f38e37d07d3; config.json b2b26a4a4a000639ad30b0c264adbefe365bdb567fbd7bb27303b8c438375bd1; viterbi_calibration.json bbc8611ef08a55ed72d64856cbbbb9a91db8dfa881f0a92e2afbad6e4bbc775a. Repacked model.onnx sha256 00f41770f92f275b9bd056ce434763856ce3191ce5eaaa4c81b8ab29908d054d. Decoding: BIOES constrained Viterbi, default operating point (all biases 0). max_length 512, stride 64, aggregation token. Label map: private_address->ADDRESS, private_date/private_url->IGNORE.
EOF
```

Build the bundle (the script writes `labels.json`, `MODEL_CARD.md`, `metrics.json`, `train_meta.json`, `sha256sums`, and
verifies the result with `scripts/ml/import_artifacts.py`; it refuses an existing output directory):

```bash
cd "$WT"
"$PY" scripts/ml/build_external_bundle.py "$MODELS/rerun_A" \
  --model "$RUN/src/pfq4/model.onnx" --tokenizer "$RUN/src/pfq4/tokenizer.json" \
  --config "$RUN/src/pfq4/config.json" --viterbi "$RUN/src/pfq4/viterbi_calibration.json" \
  --repo openai/privacy-filter --revision 7ffa9a043d54d1be65afb281eddf0ffbe629385b \
  --licence apache-2.0 --table privacy_filter --max-length 512 --stride 64 --aggregation token \
  --precision q4 --export-note "$(cat "$RUN/src/export_pf_q4.txt")" > "$RUN/build_A.log" 2>&1
echo "build A exit $?"
```

Verify the digest against the pin, and compare the bundle with the reference bench bundle if you have one:

```bash
cd "$WT"
DIG_A=$("$PY" -m pgw.ml.artifact "$MODELS/rerun_A" | "$PY" -c 'import sys,json; print(json.load(sys.stdin)["sha256"])')
echo "A digest: $DIG_A"
test "$DIG_A" = cbabf1f661ea8180a48690ae4c341a38f2ea260b0ebae88ef55770258fe46b28 && echo "A digest matches the doc pin"
```

Free the intermediate copies (the bundle has its own copy of `model.onnx`):

```bash
rm -f "$RUN/src/pfq4/model_q4.onnx" "$RUN/src/pfq4/model_q4.onnx_data" "$RUN/src/pfq4/model.onnx"
```

If `DIG_A` differs, do not edit the profile. Compare `MODEL_CARD.md`, `labels.json`, `train_meta.json` and
`sha256sums` with the reference, then the repacked `model.onnx` sha256 (00f41770...). Record what differs in your report.

## 4. Part 3: configuration B, bundle `rerun_B` (GLiNER multi PII v1, fp32)

Export with the pinned gliner (torch CPU venv). This is the only step that needs the export venv. The output must match the
sha256 of the export in the card before the bundle is built:

```bash
cat > "$RUN/src/export_gliner.py" <<'EOF'
import hashlib, sys
from pathlib import Path
from gliner import GLiNER
stage = Path(sys.argv[1]); stage.mkdir(parents=True, exist_ok=True)
m = GLiNER.from_pretrained("urchade/gliner_multi_pii-v1", revision="1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d")
m.export_to_onnx(save_dir=str(stage), onnx_filename="model.onnx", quantize=False, opset=19)
tok = m.data_processor.transformer_tokenizer
assert tok.is_fast
tok.backend_tokenizer.save(str(stage / "tokenizer.json"))
for n in ("model.onnx", "tokenizer.json"):
    print(n, hashlib.sha256((stage / n).read_bytes()).hexdigest())
EOF
cd "$RUN/src" && "$EXPORT_PY" "$RUN/src/export_gliner.py" "$RUN/src/gliner_fp32" 2>&1 | grep -v -i "warn" | tail -3
# expected: model.onnx b0d9c9fba42ef290523cd7b71d96b45cd9f1e8d94eb7db0f4287940bdecf1e4b
#           tokenizer.json c5b8041501fcdee792b9b112dd592861f04391a24633a73fd9f05aaaf6e8eff1
```

The export is done, so remove the GLiNER weights from the HF cache before the bundle copy (the disk needs the room):

```bash
PT=$("$PY" -c "import os; from huggingface_hub import snapshot_download as s; p = s(repo_id='urchade/gliner_multi_pii-v1', revision='1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d', local_files_only=True); print(os.path.realpath(os.path.join(p, 'pytorch_model.bin')))")
rm -f "$PT" && echo "removed the cached GLiNER weights (re-download with snapshot_download if you need them again)"
```

The export is inline (1.16 GB, under the protobuf limit), so it goes straight into the build; fp32 cannot be repacked and
does not need to be:

```bash
cat > "$RUN/src/export_gliner_note.txt" <<'EOF'
gliner 0.2.29 export_to_onnx (opset 19, torch 2.15 cpu) from pytorch_model.bin sha256 3003753fba99e40645cf088c7367a2c6211fc174897dc64f1f9c147c29d18d2d, gliner_config.json 2088bee85f50849dea2ab4cf411ab5cf3aaaf7c8a45c23412035978b2bc02801 at revision 1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d; encoder tokenizer from microsoft/mdeberta-v3-base@a0484667b22365f84929a935b5e50a51f71f159d (MIT). Exported model.onnx sha256 b0d9c9fba42ef290523cd7b71d96b45cd9f1e8d94eb7db0f4287940bdecf1e4b, tokenizer.json sha256 c5b8041501fcdee792b9b112dd592861f04391a24633a73fd9f05aaaf6e8eff1. fp32, no quantisation.
EOF
cd "$WT"
"$PY" scripts/ml/build_external_bundle.py "$MODELS/rerun_B" \
  --model "$RUN/src/gliner_fp32/model.onnx" --tokenizer "$RUN/src/gliner_fp32/tokenizer.json" \
  --repo urchade/gliner_multi_pii-v1 --revision 1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d \
  --licence apache-2.0 --table gliner_pii --max-length 384 --precision fp32 --threshold 0.5 \
  --export-note "$(cat "$RUN/src/export_gliner_note.txt")" > "$RUN/build_B.log" 2>&1
echo "build B exit $?"
DIG_B=$("$PY" -m pgw.ml.artifact "$MODELS/rerun_B" | "$PY" -c 'import sys,json; print(json.load(sys.stdin)["sha256"])')
echo "B digest: $DIG_B"
test "$DIG_B" = 1222c2e7e822bd9d3a569780e804798b3f6434863e9e24819ac26276ad4bd1c3 && echo "B digest matches the doc pin"
```

Free the copies once the bundle exists: `rm -rf "$RUN/src/gliner_fp32"` (the bundle keeps its own `model.onnx`).

## 5. Part 4: the profile blocks

The repository has one deployment profile, `registry/deployment.yaml`, sealed and signed by `registry/*.yaml`. That is the
convention, so no `deploy/profiles/` files were added. The block below replaces the line `  ml_detector: null` under
`settings:` in a staged copy (Part 5 does it; never edit the repository's file for a test). `min_score_by_type` is the doc's
default 0.5 for every type; `intra_op_threads: 4` is the 4-thread latency row of the doc.

Config A:

```bash
cat > "$RUN/profile_A.yaml" <<EOF
  ml_detector:
    path: $MODELS/rerun_A
    sha256: cbabf1f661ea8180a48690ae4c341a38f2ea260b0ebae88ef55770258fe46b28
    backend: token
    labels:
      private_person: PERSON
      private_email: EMAIL
      private_phone: PHONE
      secret: SECRET
      private_address: ADDRESS
      account_number: IGNORE
      private_date: IGNORE
      private_url: IGNORE
    min_score_by_type: {DEFAULT: 0.5}
    workers: 1
    queue_max: 8
    timeout_s: 5.0
    intra_op_threads: 4
    inter_op_threads: 1
    batch_size: 8
EOF
```

Config B:

```bash
cat > "$RUN/profile_B.yaml" <<EOF
  ml_detector:
    path: $MODELS/rerun_B
    sha256: 1222c2e7e822bd9d3a569780e804798b3f6434863e9e24819ac26276ad4bd1c3
    backend: span
    labels:
      person: PERSON
      email: EMAIL
      phone number: PHONE
      credit card number: CARD
      bank account number: BANK_ACCOUNT
      date of birth: DATE_OF_BIRTH
      password: SECRET
      employee id: EMPLOYEE_ID
      salary: COMPENSATION
      national id number: AADHAAR
      tax id number: PAN
      upi id: UPI_ID
      ifsc code: IFSC
      address: ADDRESS
      organization: IGNORE
      location: IGNORE
    min_score_by_type: {DEFAULT: 0.5}
    workers: 1
    queue_max: 8
    timeout_s: 5.0
    intra_op_threads: 4
    inter_op_threads: 1
    batch_size: 8
EOF
```

Config B keeps `legacy_heuristics: true` and spaCy alongside, like A. Only the detector and the bundle differ.

The staged copy also gets: `version: 5` and `config_serial: 4` (the profile changed, so both rise, OPERATIONS.md), and
`PGW_MIN_CONFIG_SERIAL=4` at start.

## 6. Part 5: keys, staged tree, seal and sign

Two different keys. Do not mix them up:

- **Source attestation key** (`source.key`): the source system's Ed25519 key. The gateway holds only its public half, in the
  contract. Production: the source owner creates it and it never leaves the source system.
- **Config signing key** (`config.key`): signs the sealed manifest. Production: created once and kept offline by the release
  approvers; the gateway gets only `PGW_TRUSTED_CONFIG_PUBKEY`, from the secret store or image, never from the repository.

For this test both are dev keys in `$RUN/keys` (mode 0600). They are created once and never overwritten.

```bash
cd "$WT"
# the keygen commands write the private key file (mode 0600, never overwritten)
[ -f "$RUN/keys/source.key" ] || "$PY" -m pgw.product.attest keygen "$RUN/keys/source.key" >/dev/null
[ -f "$RUN/keys/config.key" ] || "$PY" -m pgw.product.integrity keygen "$RUN/keys/config.key" >/dev/null
# public halves, derived from the private key files
"$PY" -c "
import sys; sys.path.insert(0, '$WT')
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
k = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(open('$RUN/keys/source.key').read().strip()))
print(k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex())" > "$RUN/keys/source.pub"
"$PY" -c "
import sys; sys.path.insert(0, '$WT')
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
k = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(open('$RUN/keys/config.key').read().strip()))
print(k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex())" > "$RUN/keys/config.pub"
[ -f "$RUN/keys/app_key.txt" ] || "$PY" -c "import secrets; print(secrets.token_hex(16))" > "$RUN/keys/app_key.txt"
[ -f "$RUN/keys/master.hex" ] || "$PY" -c "import secrets; print(secrets.token_hex(32))" > "$RUN/keys/master.hex"
chmod 600 "$RUN/keys/"*.key "$RUN/keys/app_key.txt" "$RUN/keys/master.hex"
echo "keys ready (values not printed)"
```

Stage a sealed tree per configuration. The staging script is the only place where the test-only changes happen; it is written
to `$RUN`, not into the repository:

```bash
cat > "$RUN/stage.py" <<'EOF'
"""Stage and seal a test configuration. Usage: stage.py A|B (or 0 for no ML detector, baseline)."""
import hashlib, os, shutil, sys
from pathlib import Path
run, wt = Path(os.environ["RUN"]), Path(os.environ["WT"])
sys.path.insert(0, str(wt))
from pgw.product import attest, integrity
cfg = sys.argv[1]
stage = run / f"stage_{cfg}"
shutil.rmtree(stage, ignore_errors=True)
for d in ("registry", "policies", "contracts"):
    shutil.copytree(wt / d, stage / d)
# 1. this test's source key replaces every committed DEMO key (production refuses DEMO keys)
src_pub = (run / "keys/source.pub").read_text().strip()
for f in (stage / "contracts").glob("*.yaml"):
    t = f.read_text()
    for demo in attest.DEMO_PUBKEYS:
        t = t.replace(demo, src_pub)
    f.write_text(t)
# 2. TEST ONLY: ticket_summary routes unattested input to a private model; this test has none, so refuse instead
p = stage / "contracts/ticket_summary.yaml"
t = p.read_text()
assert t.count("unattested: PRIVATE") == 1
p.write_text(t.replace("unattested: PRIVATE", "unattested: REFUSE"))
# 3. the one app of this test (key hash only; the key itself stays in keys/)
import yaml
app_key = (run / "keys/app_key.txt").read_text().strip()
apps = {"version": 1, "apps": [{"app_id": "pilot", "tenant": "acme",
        "key_sha256": hashlib.sha256(app_key.encode()).hexdigest(), "restore_output": True, "models": ["mock-1"],
        "purposes": ["ticket_summary"], "transparency": True,
        "tasks": {"ticket_summary": "../contracts/ticket_summary.yaml"}}]}
(stage / "registry/apps.yaml").write_text(yaml.safe_dump(apps, sort_keys=False))
# 4. the profile: the repository's current profile with the block for this configuration, version and serial raised
dep = (wt / "registry/deployment.yaml").read_text()
assert dep.count("\n  ml_detector: null\n") == 1 and dep.count("\nversion: 4\n") == 1 and dep.count("\nconfig_serial: 3\n") == 1
block = "" if cfg == "0" else (run / f"profile_{cfg}.yaml").read_text()
dep = dep.replace("\n  ml_detector: null\n", "\n" + block if block else "\n  ml_detector: null\n")
dep = dep.replace("\nversion: 4\n", "\nversion: 5\n").replace("\nconfig_serial: 3\n", "\nconfig_serial: 4\n")
(stage / "registry/deployment.yaml").write_text(dep)
# 5. seal and sign with the config key (the manifest covers registry/*.yaml, policies/*.yaml, contracts/*.yaml)
key = bytes.fromhex((run / "keys/config.key").read_text().strip())
(stage / "state").mkdir(exist_ok=True)
integrity.seal(stage / "policies/MANIFEST.json", sign_key=key)
print("staged", stage, "ml_detector:", "none" if not block else cfg)
EOF
cd "$WT" && "$PY" "$RUN/stage.py" A
echo "staged A"
```

`stage.py` replaces the line `  ml_detector: null` with the block. Check the result:

```bash
grep -n "ml_detector:" -A3 "$RUN/stage_A/registry/deployment.yaml" | head -6
grep -n "^version:\|^config_serial:" "$RUN/stage_A/registry/deployment.yaml"
```

## 7. Part 6: start the gateway in production mode (config A)

Production mode is `PGW_MODE=production` (the default). The gateway starts only when the manifest's Ed25519 signature
verifies under `PGW_TRUSTED_CONFIG_PUBKEY`, the rollback floor passes, every contract is sealed, and every ML pin matches.
Write the environment (secrets are read from files, never printed):

```bash
CFG=A
cat > "$RUN/gateway_env.sh" <<EOF
export PGW_MODE=production
export PGW_HOST=127.0.0.1
export PGW_PORT=$PGW_PORT
export PGW_INTEGRITY_MANIFEST=$RUN/stage_$CFG/policies/MANIFEST.json
export PGW_TRUSTED_CONFIG_PUBKEY=$(cat "$RUN/keys/config.pub")
export PGW_MIN_CONFIG_SERIAL=4
export PGW_STATE_DIR=$RUN/stage_$CFG/state
export PGW_REPLAY_DB=$RUN/stage_$CFG/replay.sqlite
export PGW_LEDGER_DB=$RUN/stage_$CFG/ledger.sqlite
export PGW_EGRESS_LEDGER=$RUN/stage_$CFG/egress.jsonl
export PGW_MASTER_KEY=$(cat "$RUN/keys/master.hex")
EOF
chmod 600 "$RUN/gateway_env.sh"
```

Start it (one configuration at a time: they share the port and the ledgers are per stage). First define the stop
helper, in the same shell you use for the rest of the runbook; it stops the gateway of `$CFG` and waits until the port is free:

```bash
stop_gateway() {
  local pid
  pid=$(cat "$RUN/gateway_$CFG.pid" 2>/dev/null) || { echo "no gateway_$CFG.pid"; return 0; }
  kill "$pid" 2>/dev/null
  for i in $(seq 1 60); do curl -s -m 1 "http://127.0.0.1:$PGW_PORT/healthz" >/dev/null || break; sleep 1; done
  if curl -s -m 1 "http://127.0.0.1:$PGW_PORT/healthz" >/dev/null; then
    echo "port $PGW_PORT still answers: another gateway runs there; stop it before continuing"; return 1
  fi
  echo "$CFG stopped, port $PGW_PORT free"
}
```

Then start, unless the port already answers (an old gateway would otherwise pass the health check for the new one):

```bash
if curl -s -m 1 "http://127.0.0.1:$PGW_PORT/healthz" >/dev/null; then
  echo "port $PGW_PORT already answers: stop that gateway first (stop_gateway)"
else
  cd "$WT" && ( set -a; . "$RUN/gateway_env.sh"; set +a; exec "$PY" -m pgw.gateway ) > "$RUN/gateway_$CFG.log" 2>&1 &
  echo $! > "$RUN/gateway_$CFG.pid"
  for i in $(seq 1 180); do
    curl -sf http://127.0.0.1:$PGW_PORT/healthz && break
    kill -0 "$(cat "$RUN/gateway_$CFG.pid")" 2>/dev/null || { echo "gateway exited:"; tail -3 "$RUN/gateway_$CFG.log"; break; }
    sleep 1
  done; echo
fi
```

Expected: `{"status":"ok","mode":"production"}` (the healthz body is counts and status only; it does not say which
detector is loaded). The log should show no `ml_detector pool failed` line and no `ner_replaced_by_ml_detector`.

## 8. Part 7: confirm the detector is active, and the smoke test

Three checks, in this order:

1. **Worker processes are loaded.** The detector runs in its own worker process(es), each with its model:

   ```bash
   ps -eo pid,ppid,rss,args | grep -E "pgw.gateway|multiprocessing|spawn_main" | grep -v grep
   ```

   Expect one extra child process beyond the spaCy workers. Its RSS is the model: about 2.7 GB for privacy-filter q4 (A), about
   3.4 GB for GLiNER fp32 (B), plus the resident spaCy workers.

2. **The smoke ticket over HTTP** (the real server, the attested ticket, the same path as production traffic):

   ```bash
   cat > "$RUN/smoke_http.py" <<'EOF'
   import json, os, sys, urllib.request
   from pathlib import Path
   run, wt = Path(os.environ["RUN"]), Path(os.environ["WT"])
   sys.path.insert(0, str(wt))
   from pgw.product import attest
   TICKET = ("Hi, I'm Kavya Raghunathan, my new address is 14 Lake View Road, Indiranagar, Bengaluru 560038, "
             "phone 98450 12345")
   body = {"model": "mock-1", "messages": [{"role": "user", "content": TICKET}],
           "pgw_subjects": {"requester": {"name": "Kavya Raghunathan"}}}
   priv = (run / "keys/source.key").read_text().strip()
   app_key = (run / "keys/app_key.txt").read_text().strip()
   headers = {"Authorization": f"Bearer {app_key}", "X-PGW-Purpose": "ticket_summary",
              "Content-Type": "application/json"}
   headers.update(attest.sign_body(priv, "ticket_summary", "helpdesk-ticket-export@1", body, app_id="pilot"))
   req = urllib.request.Request("http://127.0.0.1:" + os.environ.get("PGW_PORT", "8081") + "/v1/chat/completions", data=json.dumps(body).encode(),
                                headers=headers, method="POST")
   try:
       r = urllib.request.urlopen(req, timeout=60); status, raw = r.status, r.read()
   except urllib.error.HTTPError as e:
       status, raw = e.code, e.read()
   out = {"status": status}
   if status == 200:
       j = json.loads(raw)
       rep = j.get("pgw_report", {})
       out["checks"] = {c["id"]: c["status"] for c in rep.get("checks", [])}
       out["reply"] = j["choices"][0]["message"]["content"]
   else:
       out["error"] = raw[:300].decode("utf-8", "replace")
   print(json.dumps(out, indent=1, ensure_ascii=False))
   EOF
   "$PY" "$RUN/smoke_http.py"
   ```

   Expected: `"status": 200`; every check `pass`; the reply restores the person, the address and the phone for the app (the
   restore step), while the upstream saw placeholders (check 3).

3. **What left the gateway.** The mock upstream keeps the exact bytes it received in memory, so this check runs in-process
   against the same stage, after the server is stopped (the two share the ledgers, and the ledger is single-writer):

   ```bash
   stop_gateway
   cat > "$RUN/smoke_inproc.py" <<'EOF'
   import dataclasses, json, os, sys
   from pathlib import Path
   def main():
       run, wt = Path(os.environ["RUN"]), Path(os.environ["WT"])
       sys.path.insert(0, str(wt))
       from starlette.testclient import TestClient
       from pgw.gateway.app import create_app
       from pgw.gateway.settings import Settings
       from pgw.gateway.upstream import MockLLMUpstream
       from pgw.product import attest
       stage_cfg = os.environ["CFG"]
       ext = MockLLMUpstream("summarise")
       # the same environment as the server; the upstream is the only code-injected object (as in tests/)
       s = dataclasses.replace(Settings.from_env(), upstream=ext)
       TICKET = ("Hi, I'm Kavya Raghunathan, my new address is 14 Lake View Road, Indiranagar, Bengaluru 560038, "
                 "phone 98450 12345")
       body = {"model": "mock-1", "messages": [{"role": "user", "content": TICKET}],
               "pgw_subjects": {"requester": {"name": "Kavya Raghunathan"}}}
       priv = (run / "keys/source.key").read_text().strip()
       app_key = (run / "keys/app_key.txt").read_text().strip()
       h = {"Authorization": f"Bearer {app_key}", "X-PGW-Purpose": "ticket_summary"}
       h.update(attest.sign_body(priv, "ticket_summary", "helpdesk-ticket-export@1", body, app_id="pilot"))
       with TestClient(create_app(s)) as client:
           r = client.post("/v1/chat/completions", json=body, headers=h)
       print(json.dumps({"config": stage_cfg, "status": r.status_code,
                         "LEFT_THE_GATEWAY": ext.received[-1].decode("utf-8") if ext.received else None},
                        indent=1, ensure_ascii=False))
   if __name__ == "__main__":
       main()
   EOF
   CFG=$CFG; ( set -a; . "$RUN/gateway_env.sh"; set +a; CFG=$CFG "$PY" "$RUN/smoke_inproc.py" ) 2>/dev/null
   ```

   Expected for A (observed, clean run): the name, the whole street address and the phone are placeholders; the postal code
   is inside the address placeholder:
   `"Hi, I'm [PERSON_09JGMZ], my new address is [ADDRESS_4ZN2DJ], phone [PHONE_0NCP1E]"`.

Stop A before B:

```bash
stop_gateway
```

## 9. Part 6 and 7 for configuration B

Stage and start the same way with `CFG=B`. The stage and env files are per configuration.

```bash
cd "$WT" && "$PY" "$RUN/stage.py" B
CFG=B
sed -e "s#stage_A#stage_B#g" -e "s#^export PGW_PORT=.*#export PGW_PORT=$PGW_PORT#" "$RUN/gateway_env.sh" > "$RUN/gateway_env_B.sh"
cd "$WT" && ( set -a; . "$RUN/gateway_env_B.sh"; set +a; exec "$PY" -m pgw.gateway ) > "$RUN/gateway_$CFG.log" 2>&1 &
echo $! > "$RUN/gateway_$CFG.pid"
for i in $(seq 1 180); do curl -sf http://127.0.0.1:$PGW_PORT/healthz && break; sleep 1; done; echo
"$PY" "$RUN/smoke_http.py"
```

Then stop it and run the in-process check with B's environment:

```bash
stop_gateway
( set -a; . "$RUN/gateway_env_B.sh"; set +a; CFG=$CFG "$PY" "$RUN/smoke_inproc.py" ) 2>/dev/null
stop_gateway
```

Expected for B: the same three kinds of placeholder, the whole address in one `[ADDRESS_...]`. Observed in an earlier
attempt (a second gateway was still alive on the same ledgers, so treat it as indicative): `"Hi, I'm [PERSON_94SW8F], my
new address is [ADDRESS_795F2S], phone [PHONE_2D2G5H]"`. The in-process capture for B was not repeated after the port fix,
because the server that is running holds the ledgers; run the block above after `stop_gateway` to confirm on your stage.

**Baseline for comparison (no ML detector).** Stage `0` writes the current profile with `ml_detector: null`. Observed with an
in-process driver on a test-sealed tree of the same profile (not part of this runbook): the spaCy-only stack sent
`"Hi, I'm [PERSON_7HH179], my new address is 14 Lake View Road, Indiranagar, Bengaluru [ADDRESS_6C8YPP], phone [PHONE_7R8Q5H]"`,
so the street address left the gateway in clear. That difference is what the open detector is for.

## 10. Part 8: run your sets (release role, once per candidate)

Your sets are not in this repository and are not read by anything here. Keep them out of `$RUN` as well; point `--data` at
them where they are. Use the release role only, once per candidate, with thresholds fixed beforehand (ML_EVALUATION.md
sections 3 and 4). The harness prints aggregates only and appends to `eval/ml/results/release_runs.jsonl`.

- `pipeline_ml` is the current stack plus the ML detector (spaCy alongside, legacy heuristics as in the profile).
- `ml_checksum` is the detector plus the checksum regexes only (`legacy_heuristics: false` in a signed profile needs its own
  stage; not part of these two configurations).

Write the detector mapping as a standalone file (the same keys as in the profile, without the `ml_detector:` key):

```bash
cd "$WT"
"$PY" - <<'EOF'
import os, yaml
from pathlib import Path
run = Path(os.environ["RUN"])
for cfg in ("A", "B"):
    block = yaml.safe_load((run / f"profile_{cfg}.yaml").read_text())["ml_detector"]
    (run / f"ml_{cfg}.yaml").write_text(yaml.safe_dump(block, sort_keys=False))
print("wrote", run / "ml_A.yaml", run / "ml_B.yaml")
EOF
: "${YOUR_SET:?set YOUR_SET to the path of your release set (a file you hold, not in the repository)}"
# config A
"$PY" -m eval.ml.run --role release --data "$YOUR_SET" --format human \
  --backend pipeline_ml --ner en_core_web_lg --model "$RUN/ml_A.yaml" --gate eval/ml/release_gate.yaml \
  --out "$RUN/result_A.json"
# config B
"$PY" -m eval.ml.run --role release --data "$YOUR_SET" --format human \
  --backend pipeline_ml --ner en_core_web_lg --model "$RUN/ml_B.yaml" --gate eval/ml/release_gate.yaml \
  --out "$RUN/result_B.json"
```

`$YOUR_SET` is your file and `--format` follows your file (`pgw`, `bio` or `human`). Thresholds that are null in
`eval/ml/release_gate.yaml` make the verdict UNDECIDED; that is not a failure and is not something to fill in during the run.

Note: the harness's `--model` expects the mapping; `ml_A.yaml` / `ml_B.yaml` are the same values as the profile blocks in
section 5, de-indented.

## 11. Resource use (from ML_EVALUATION.md, this 4-core VM)

| bundle | 1 thread | 4 threads | peak RSS per worker (incl. load) |
|---|---|---|---|
| privacy-filter q4 (A) | 1164 ms | 440 ms (about 0.44 s per 512 tokens) | 2.7 GB |
| GLiNER fp32 (B) | 3976 ms | 1561 ms (about 1.6 s) | 3.4 GB |

- Every worker holds its own full copy of the model. `workers: 1` here is one copy. Each extra worker adds one more RSS
  figure above; `workers x intra_op_threads` must stay within the cores (ML_ARCHITECTURE.md section 6).
- The spaCy NER workers (`ner_workers: 4` in the profile) are separate, about the same as before this change.
- Per request, both are CPU-bound: a 512-token window costs the figures above; the request deadline is `protect_timeout_s`
  (10 s) and the detector's own `timeout_s` (5 s here).

## 12. Failure notes

- **Digest mismatch at startup** (`ml_detector` `sha256`): the bundle differs from the pin. Compare the bundle's files with
  the reference bundle, not the profile. Do not change the pin to the new digest.
- **`pool failed to start ... worker died while starting`**: usually a bundle that cannot be loaded (wrong path, missing file,
  too little RAM). Check `$RUN/gateway_$CFG.log` and free RAM.
- **`ContractError ... routes unattested input privately`**: the staged contract was not changed to `REFUSE` (Part 5 step 2).
- **Refused source signature** (`bad_signature`, `stale_timestamp`): the signing key and the contract's key do not match, or the
  clock is more than 300 s off.
- **`ledger_unavailable` / 503 after a restart**: the ledger file was changed while another process held it. Stop every
  gateway and in-process run before starting another.
- **Disk full during export or build**: see the disk note in section 0.
- **HF CAS error when downloading**: set `HF_HUB_DISABLE_XET=1` (section 0).

## 13. Cleanup

```bash
kill "$(cat "$RUN/gateway_A.pid" 2>/dev/null)" "$(cat "$RUN/gateway_B.pid" 2>/dev/null)" 2>/dev/null; true
rm -rf "$RUN/stage_0" "$RUN/src/gliner_fp32" "$RUN/src/pfq4/model.onnx"
```

Keep `$MODELS/rerun_A`, `$MODELS/rerun_B` and `$RUN/keys` for the report, or delete the keys when you are done with
them. Keys stay in `$RUN` (mode 0600), not in the repository.

## 14. Verification record (the VM that wrote this file)

- **Digests reproduce.** Bundle A (`cbabf1f6...`) and bundle B (`1222c2e7...`) were rebuilt from the pinned revisions. Every file
  of each bundle is byte-identical to the bench bundle (`pf_q4_v2`, `gliner_fp32_v2`), including `MODEL_CARD.md`. The source
  files hash to the card values, the repacked privacy-filter model is `00f41770...` with parity max abs diff 0, and the GLiNER
  export hashes to `b0d9c9...` (model) and `c5b80415...` (tokenizer).
- **Configuration A.** Started in production mode (`/healthz` `{"status":"ok","mode":"production"}`); the attested smoke ticket
  returned HTTP 200 with all five checks passing; the in-process capture sent `Hi, I'm [PERSON_09JGMZ], my new address is
  [ADDRESS_4ZN2DJ], phone [PHONE_0NCP1E]`.
- **Configuration B.** Started in production mode on a separate port, the attested smoke ticket returned HTTP 200 with all
  five checks passing. The in-process capture ran once in an earlier attempt (placeholders as in section 9) and was not
  repeated.
- **Not verified here.** Your own sets (section 10) and the in-process capture for B after a clean start.
- **Failures met and fixed in this runbook.** (1) Disk full during a bundle copy: intermediate copies are now removed as soon
  as they are not needed (sections 0, 2, 3, 4). (2) A failed build left a partial bundle directory: remove it before a retry.
  (3) The gateway start raced with the previous gateway still shutting down on the same port: the health check passed against
  the old process and the new one failed to bind; `stop_gateway` now waits for the port, and the start refuses a port that
  already answers. (4) An unset release-set variable: now a guard with a message.
- **Environment.** HF xet transfers failed with a CAS error here; `HF_HUB_DISABLE_XET=1` was used throughout.
- **Layout decision.** No `deploy/profiles/` files were added: the repository's profile convention is the sealed
  `registry/deployment.yaml`, and the blocks in section 5 are its `ml_detector` entries.
