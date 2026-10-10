#!/usr/bin/env bash
# Exercises THREAT_MODEL T13 network controls in deploy/docker-compose.yml with real containers.
# Prints PASS/FAIL per check; exit 1 if any FAIL. Uses dummy credentials only.
# Env: CA_BUNDLE (default /root/.ccr/ca-bundle.crt) is mounted in the gateway ONLY because the
# sandbox egress re-terminates TLS; on a normal host drop that mount and the CA arg.
set -u
cd "$(dirname "$0")"
export ANTHROPIC_API_KEY=dummy-not-a-real-key PGW_MASTER_KEY=dummy-master-key PGW_TRUSTED_CONFIG_PUBKEY=dummy-topology-test-only
export COMPOSE_PROJECT_NAME=pgwverify
DC=(docker compose -f docker-compose.yml -f docker-compose.verify.yml)
FAILS=0
res() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
APP()  { "${DC[@]}" exec -T example-app "$@"; }
GW()   { "${DC[@]}" exec -T gateway "$@"; }
trap '"${DC[@]}" down -v --remove-orphans >/dev/null 2>&1' EXIT

"${DC[@]}" up -d >/dev/null 2>&1 || { echo "FAIL  compose up"; exit 1; }
sleep 3

# Python probe: prints OK <detail> or ERR <exact exception>; exit 0 iff reached.
HTTPS_PROBE='
import sys,socket,ssl,urllib.request
u=sys.argv[1]
try:
    r=urllib.request.urlopen(u,timeout=6,context=ssl.create_default_context(cafile=sys.argv[2] if len(sys.argv)>2 else None))
    print("OK HTTP",r.status); 
except Exception as e:
    print("ERR",type(e).__name__,e); sys.exit(1)'
TCP_PROBE='
import sys,socket
try:
    s=socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=6); print("OK connected"); 
except Exception as e:
    print("ERR",type(e).__name__,e); sys.exit(1)'
DNS_PROBE='
import sys,socket
q=bytes.fromhex("abcd01000001000000000000")+b"\x07example\x03com\x00"+bytes.fromhex("00010001")
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(5)
try:
    s.sendto(q,(sys.argv[1],53)); d=s.recv(512); print("OK dns answer",len(d),"bytes")
except Exception as e:
    print("ERR",type(e).__name__,e); sys.exit(1)'

echo "== topology"
for s in example-app gateway; do
  echo "$s networks: $(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$("${DC[@]}" ps -q $s)")"
done
docker network inspect pgwverify_appnet -f 'appnet internal={{.Internal}}'
docker network inspect pgwverify_egressnet -f 'egressnet internal={{.Internal}}'

echo "== checks"
out=$(APP python -c "$HTTPS_PROBE" https://example.com 2>&1); rc=$?; echo "   app->https://example.com: $out"; [ $rc != 0 ]; res $? "1a app cannot reach https://example.com"
out=$(APP python -c "$HTTPS_PROBE" https://api.groq.com 2>&1); rc=$?; echo "   app->https://api.groq.com: $out"; [ $rc != 0 ]; res $? "1b app cannot reach https://api.groq.com"
out=$(APP python -c "$HTTPS_PROBE" https://api.anthropic.com 2>&1); rc=$?; echo "   app->https://api.anthropic.com: $out"; [ $rc != 0 ]; res $? "1c app cannot reach https://api.anthropic.com"

out=$(APP python -c "$HTTPS_PROBE" http://gateway:8080/ 2>&1); rc=$?; echo "   app->http://gateway:8080/: $out"; [ $rc = 0 ]; res $? "2 app reaches gateway on appnet (alias 'gateway')"

out=$(GW python -c "$HTTPS_PROBE" https://example.com /ca.crt 2>&1); rc=$?; echo "   gateway->https://example.com: $out"; echo "$out" | grep -qE '^OK|^ERR HTTPError'; res $? "3a gateway reaches external https (any HTTP response; 403 here = sandbox egress policy, not a network failure)"
out=$(GW python -c "$HTTPS_PROBE" https://api.anthropic.com /ca.crt 2>&1); echo "   gateway->https://api.anthropic.com (any HTTP status = reachable): $out"
echo "$out" | grep -qE '^OK|HTTP Error (401|403|404|405)'; res $? "3b gateway reaches approved provider host api.anthropic.com"

EXTIP=$(GW python -c 'import socket;print(socket.gethostbyname("example.com"))' 2>/dev/null)
echo "   example.com resolved (from gateway) = $EXTIP"
out=$(APP python -c "$TCP_PROBE" "$EXTIP" 443 2>&1); rc=$?; echo "   app->$EXTIP:443: $out"; [ $rc != 0 ]; res $? "4a bypass: direct external IP:443"
out=$(APP python -c "$TCP_PROBE" 1.1.1.1 443 2>&1); rc=$?; echo "   app->1.1.1.1:443: $out"; [ $rc != 0 ]; res $? "4b bypass: direct 1.1.1.1:443"
out=$(APP python -c "$DNS_PROBE" 8.8.8.8 2>&1); rc=$?; echo "   app->8.8.8.8:53/udp: $out"; [ $rc != 0 ]; res $? "4c bypass: DNS via 8.8.8.8"
out=$(APP python -c "import socket;print(socket.gethostbyname('example.com'))" 2>&1); rc=$?; echo "   app resolve example.com: $out"; [ $rc != 0 ]; res $? "4d app cannot resolve external names"
PROXY_HOSTPORT=${HTTPS_PROXY#http://}; PPORT=${PROXY_HOSTPORT##*:}
# NOTE: the host-side bridge IP of an internal network stays reachable (host services bound to 0.0.0.0).
for gwip in $(docker network inspect pgwverify_appnet pgwverify_egressnet -f '{{range .IPAM.Config}}{{.Gateway}} {{end}}'); do
  out=$(APP env HTTPS_PROXY=http://$gwip:$PPORT python -c "$HTTPS_PROBE" https://example.com 2>&1); rc=$?
  echo "   app, HTTPS_PROXY=http://$gwip:$PPORT -> https://example.com: $out"; [ $rc != 0 ]; res $? "4e bypass: HTTPS_PROXY to host/sandbox proxy via bridge gw $gwip"
done
out=$(APP env HTTPS_PROXY=$HTTPS_PROXY python -c "$HTTPS_PROBE" https://example.com 2>&1); rc=$?
echo "   app, HTTPS_PROXY=$HTTPS_PROXY (host loopback) -> https://example.com: $out"; [ $rc != 0 ]; res $? "4f bypass: HTTPS_PROXY=sandbox proxy address 127.0.0.1"

echo "== result: $FAILS failed"; [ "$FAILS" = 0 ]
