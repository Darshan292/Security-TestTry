"""integration_debug templates (v2): stack traces, HTTP dumps, curl, URIs with credentials, config files, CI logs, SQL."""
from __future__ import annotations

from .core import (E, gen_aadhaar, fmt_aadhaar, gen_acct, gen_ifsc, gen_pan, gen_card, gen_secret, jdump, tpl, rand_date, fmt_date, ALNUM, LOW, DIG)

S = "integration_debug"
HOSTS = ["db-prod-3.internal", "hrms-pg.ap-south-1.rds.amazonaws.com", "10.20.4.17", "cache.kestrel.local", "mq-01.corp.kestrelsys.in",
         "payroll-mysql.internal", "sftp.bankgw.example.co.in", "cluster0.x7k2p.mongodb.net", "redis-hr.ap-south-1.cache.amazonaws.com"]
SVC = ["svc_hrms", "payroll_rw", "etl_user", "airflow", "reporting_ro", "hr_batch", "kestrel_app", "bankfile"]


def uid_(g):
    return "".join(g.r.choice("0123456789abcdef") for _ in range(8)) + "-" + "".join(g.r.choice("0123456789abcdef") for _ in range(4))


def pw(g):
    return g.secret("password", uri_safe=True)


def uri(g, scheme=None):
    b, r = g.b, g.r
    s = scheme or r.choice(["postgres", "mysql", "mongodb+srv", "redis", "amqp", "sftp", "postgresql", "mongodb", "rediss", "amqps", "jdbc:postgresql", "jdbc:mysql", "ftp"])
    h, u = r.choice(HOSTS), r.choice(SVC)
    if s.startswith("jdbc"):
        db = "hrms"
        port = 5432 if "postgres" in s else 3306
        if r.random() < 0.5:
            b(s, "://", h, ":", str(port), "/", db, "?user=", u, "&password=", pw(g), "&ssl=true")
        else:
            b(s, "://", u, ":", pw(g), "@", h, ":", str(port), "/", db)
    elif s == "redis" or s == "rediss":
        b(s, "://:", pw(g), "@", h, ":6379/", str(r.randint(0, 5)))
    elif s.startswith("amqp"):
        b(s, "://", u, ":", pw(g), "@", h, ":5672/hr")
    elif s.startswith("mongodb"):
        b(s, "://", u, ":", pw(g), "@", h, "/hrms?retryWrites=true&w=majority")
    elif s in ("sftp", "ftp"):
        b(s, "://", u, ":", pw(g), "@", h, "/outbound/payroll")
    else:
        b(s, "://", u, ":", pw(g), "@", h, ":", str(5432 if "postgres" in s else 3306), "/hrms")


@tpl(S, "conn_uri_sentences")
def _(g):
    b, r = g.b, g.r
    b(g.ts(" ", False), " ", g.pick("INFO", "WARN", "ERROR"), " [main] ", g.pick("DataSourceFactory", "PoolInit", "BootstrapConfig"), " - ", g.pick("connecting to ", "retrying ", "failed to open "))
    uri(g)
    b("\n")
    if r.random() < 0.6:
        b(g.ts(" ", False), " WARN fallback replica: ")
        uri(g)
        b("\n")


@tpl(S, "conn_uri_cli_history")
def _(g):
    b, r = g.b, g.r
    b("$ psql \"")
    uri(g, "postgresql")
    b("\" -c 'select count(*) from employees'\n$ mongosh \"")
    uri(g, "mongodb+srv")
    b("\"\n$ redis-cli -u ")
    uri(g, "redis")
    b(" ping\nPONG\n")


@tpl(S, "jdbc_properties_file")
def _(g):
    b, r = g.b, g.r
    b("# hrms datasource - do not commit\nspring.datasource.url=")
    uri(g, g.pick("jdbc:postgresql", "jdbc:mysql"))
    b("\nspring.datasource.username=", g.pick(*SVC), "\nspring.datasource.password=", pw(g), "\nspring.datasource.hikari.maximum-pool-size=", str(r.randint(5, 40)),
      "\nspring.rabbitmq.addresses=")
    uri(g, "amqp")
    b("\nhr.mail.from=", g.mail(g.person("roster")), "\n")


@tpl(S, "dotenv_file")
def _(g):
    b, r = g.b, g.r
    b("NODE_ENV=production\nPORT=", str(r.randint(3000, 9000)), "\nDATABASE_URL=")
    uri(g, "postgres")
    b("\nREDIS_URL=")
    uri(g, "redis")
    b("\nJWT_SECRET=", g.secret("opaque"), "\nSENDGRID_API_KEY=", g.secret("sendgrid"), "\nAWS_ACCESS_KEY_ID=", g.secret("aws_key"), "\nAWS_SECRET_ACCESS_KEY=", g.secret("aws_secret"),
      "\nLOG_LEVEL=info\nPAYROLL_ADMIN_EMAIL=", g.mail(g.person("roster")), "\n")


@tpl(S, "k8s_secret_yaml")
def _(g):
    b, r = g.b, g.r
    b("apiVersion: v1\nkind: Secret\nmetadata:\n  name: hrms-", g.pick("api", "batch", "payroll"), "\n  namespace: prod\ntype: Opaque\nstringData:\n")
    b("  DB_PASSWORD: \"", pw(g), "\"\n  API_TOKEN: ", g.secret(g.pick("gh_pat", "opaque", "slack_bot")), "\n  SMTP_PASS: '", pw(g), "'\n")
    b("  CONFIG_URL: ")
    uri(g, "mongodb+srv")
    b("\n")


@tpl(S, "yaml_app_config")
def _(g):
    b, r = g.b, g.r
    b("integrations:\n  bank:\n    endpoint: https://api.", g.pick("hdfc", "icici", "axis"), "bank.example.in/v2/payouts\n    client_id: kestrel-hr\n    client_secret: ", g.secret("opaque"),
      "\n  sftp:\n    url: ")
    uri(g, "sftp")
    b("\n  notify:\n    to: [", g.mail(g.person("roster")), ", ", g.mail(g.person("roster")), "]\n    slack_webhook: https://hooks.slack.com/services/T", "".join(r.choice("ABCDEFGH0123456789") for _ in range(8)), "/B",
      "".join(r.choice("ABCDEFGH0123456789") for _ in range(8)), "/", g.secret("opaque"), "\n")


@tpl(S, "python_traceback_payload")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    b("Traceback (most recent call last):\n  File \"/app/payroll/loader.py\", line ", str(r.randint(20, 400)), ", in load_row\n    validate(row)\n  File \"/app/payroll/validate.py\", line ", str(r.randint(10, 200)), ", in validate\n    raise ValueError(f\"bad PAN for {row['emp']}\")\n")
    b("ValueError: bad PAN for ", eid_(p), "\nrow = {'emp': '", eid_(p), "', 'name': '", g.nm(p), "', 'pan': '", E(gen_pan(r, p), "PAN"), "', 'aadhaar': '", E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR"),
      "', 'email': '", g.mail(p), "', 'ctc': ", E(str(r.randint(30, 4000) * 1000), "COMPENSATION"), "}\n")


def eid_(p):
    return E(p.emp_id, "EMPLOYEE_ID")


@tpl(S, "java_exception_sql")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    b("org.springframework.dao.DuplicateKeyException: PreparedStatementCallback; SQL [INSERT INTO emp_bank (emp_id, acct_no, ifsc) VALUES ('", eid_(p), "', '", E(gen_acct(r), "BANK_ACCOUNT"), "', '", E(gen_ifsc(r), "IFSC"), "')]\n")
    b("\tat org.springframework.jdbc.support.SQLErrorCodeSQLExceptionTranslator.doTranslate(SQLErrorCodeSQLExceptionTranslator.java:", str(r.randint(200, 300)), ")\n\tat com.kestrel.hrms.bank.BankRepo.save(BankRepo.java:", str(r.randint(30, 120)), ")\nCaused by: java.sql.SQLIntegrityConstraintViolationException: Duplicate entry '",
      E(gen_acct(r), "BANK_ACCOUNT"), "' for key 'uq_acct'\n")


@tpl(S, "http_request_response_dump")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    b("POST /api/v1/employees/", eid_(p), "/bank HTTP/1.1\nHost: hrms.", g.pick("kestrelsys.in", "internal.kestrelsys.in"), "\nAuthorization: Bearer ", g.secret("jwt"), "\nX-Request-Id: ", uid_(g), "\nContent-Type: application/json\n\n")
    jdump(b, {"holder": g.nm(p, "upper"), "account": E(gen_acct(r), "BANK_ACCOUNT"), "ifsc": E(gen_ifsc(r), "IFSC"), "notify": g.mail(p)}, None)
    b("\n\nHTTP/1.1 422 Unprocessable Entity\nContent-Type: application/json\n\n{\"error\":\"ifsc_mismatch\"}")


@tpl(S, "curl_api_key_body")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    b("curl -sS -X POST https://api.payroll-vendor.example.com/v3/employees \\\n  -H 'X-API-Key: ", g.secret("hex32"), "' \\\n  -H 'Content-Type: application/json' \\\n  -d '")
    jdump(b, {"id": eid_(p), "pan": E(gen_pan(r, p), "PAN"), "mobile": g.phone(p, 0), "salary": g.money("monthly", 2)}, None)
    b("'\n")


@tpl(S, "curl_basic_auth_and_token")
def _(g):
    b, r = g.b, g.r
    b("curl -u ", g.pick(*SVC), ":", pw(g), " https://sftp-gw.example.in/api/files\ncurl -H \"Authorization: token ", g.secret("gh_pat"), "\" https://api.github.com/repos/kestrel/hr-exports/contents\n")
    b("curl --header 'Authorization: Bearer ", g.secret("opaque"), "' 'https://hooks.example.in/notify?to=", E(g.ext_email(g.person("external")), "EMAIL"), "'\n")


@tpl(S, "gh_actions_log")
def _(g):
    b, r = g.b, g.r
    b("##[group]Run ./scripts/deploy.sh\nenv:\n  AWS_ACCESS_KEY_ID: ", g.secret("aws_key"), "\n  AWS_SECRET_ACCESS_KEY: ", g.secret("aws_secret"), "\n  COMMIT_SHA: ", "".join(r.choice("0123456789abcdef") for _ in range(40)), "\n##[endgroup]\n")
    b("Deploying build ", str(r.randint(100, 9999)), " to prod...\nnotify: sending report to ", g.mail(g.person("roster")), "\nError: Process completed with exit code 1.\n")


@tpl(S, "jenkins_console")
def _(g):
    b, r = g.b, g.r
    b("Started by user ", g.nm(g.person("roster"), "lower"), "\n[Pipeline] withCredentials\nMasking supported pattern matches of $NEXUS_PASS\n[Pipeline] sh\n+ docker login -u ci-bot -p ", pw(g), " registry.kestrel.local\n")
    b("WARNING! Using --password via the CLI is insecure.\n+ git clone https://ci-bot:", g.secret("gh_pat"), "@github.com/kestrel/hrms.git\nFinished: FAILURE\n")


@tpl(S, "sql_insert_dump")
def _(g):
    b, r = g.b, g.r
    for _ in range(r.randint(2, 4)):
        p = g.person("roster")
        b("INSERT INTO employee_kyc (emp_no, full_name, pan, aadhaar, dob, bank_acct, ifsc) VALUES ('", eid_(p), "', '", g.nm(p, "full"), "', '", E(gen_pan(r, p), "PAN"), "', '",
          E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR"), "', '", g.dob(2), "', '", E(gen_acct(r), "BANK_ACCOUNT"), "', '", E(gen_ifsc(r), "IFSC"), "');\n")


@tpl(S, "sql_update_where_debug")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    b("-- fix for ticket HRD-", str(r.randint(1000, 9999)), "\nUPDATE payroll_master SET bank_acct_no = '", E(gen_acct(r), "BANK_ACCOUNT"), "', card_no = '", E(gen_card(r, 0), "CARD"), "' WHERE emp_no = '", eid_(p),
      "' AND email = '", g.mail(p), "';\nSELECT * FROM users WHERE api_token = '", g.secret("opaque"), "';\n")


@tpl(S, "terraform_tfvars")
def _(g):
    b, r = g.b, g.r
    b("region = \"ap-south-1\"\ndb_master_username = \"hrmsadmin\"\ndb_master_password = \"", pw(g), "\"\nsmtp_password = \"", g.secret("password2"), "\"\nalert_email = \"", g.mail(g.person("roster")), "\"\ntags = { owner = \"people-tech\", env = \"prod\" }\n")


@tpl(S, "docker_compose_env")
def _(g):
    b, r = g.b, g.r
    b("services:\n  api:\n    image: registry.kestrel.local/hrms-api:", str(r.randint(1, 9)), ".", str(r.randint(0, 20)), ".", str(r.randint(0, 9)), "\n    environment:\n      - SPRING_DATASOURCE_PASSWORD=", pw(g), "\n      - PAYROLL_API_KEY=", g.secret("stripe"),
      "\n      - SUPPORT_MAILBOX=", g.mail(g.person("roster")), "\n    ports:\n      - \"8080:8080\"\n")


@tpl(S, "postman_collection_json")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    d = {"info": {"name": "HRMS sandbox", "_postman_id": uid_(g)}, "auth": {"type": "bearer", "bearer": [{"key": "token", "value": g.secret("jwt"), "type": "string"}]},
         "variable": [{"key": "empId", "value": eid_(p)}, {"key": "owner", "value": g.mail(p)}, {"key": "pan", "value": E(gen_pan(r, p), "PAN")}]}
    jdump(b, d, 2)


@tpl(S, "splunk_json_log")
def _(g):
    b, r = g.b, g.r
    for _ in range(r.randint(2, 4)):
        p = g.person()
        jdump(b, {"ts": g.ts(), "level": g.pick("INFO", "WARN"), "svc": "hr-gateway", "msg": g.pick("otp sent", "profile lookup", "kyc submitted"), "user": g.nm(p, "full"), "mobile": g.phone(p, 5),
                  "trace_id": "".join(r.choice("0123456789abcdef") for _ in range(32))}, None)
        b("\n")


@tpl(S, "kafka_message_dump")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    b("topic=hr.employee.updated partition=", str(r.randint(0, 11)), " offset=", str(r.randint(100000, 9999999)), " key=", eid_(p), "\nvalue=")
    jdump(b, {"schema": "v3", "after": {"name": g.nm(p, "lf"), "dob": g.dob(1), "uan_mobile": g.phone(p, 1), "sal": g.money("annual", 0)}}, None)
    b("\n")


@tpl(S, "nginx_access_log")
def _(g):
    b, r = g.b, g.r
    for _ in range(r.randint(3, 5)):
        p = g.person()
        b(f"{r.randint(10, 200)}.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(1, 254)} - - [{rand_date(r, 2026, 2026).strftime('%d/%b/%Y')}:{r.randint(0, 23):02d}:{r.randint(0, 59):02d}:{r.randint(0, 59):02d} +0530] \"GET /verify?email=",
          g.mail(p), "&mobile=", g.phone(p, 5), " HTTP/1.1\" 200 ", str(r.randint(100, 9000)), " \"-\" \"Mozilla/5.0\"\n")


@tpl(S, "sentry_event")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    d = {"event_id": "".join(r.choice("0123456789abcdef") for _ in range(32)), "user": {"id": eid_(p), "email": g.mail(p), "username": g.nm(p, "lower")}}
    jdump(b, {"event_id": d["event_id"], "platform": "python", "user": d["user"], "extra": {"api_key": g.secret("google"), "session": g.secret("opaque")}}, 2)


@tpl(S, "cred_files_aws_pgpass")
def _(g):
    b, r = g.b, g.r
    b("# ~/.aws/credentials\n[default]\naws_access_key_id = ", g.secret("aws_key"), "\naws_secret_access_key = ", g.secret("aws_secret"), "\n\n# ~/.pgpass\n", g.pick(*HOSTS), ":5432:hrms:", g.pick(*SVC), ":", pw(g), "\n\n# ~/.npmrc\n//registry.npmjs.org/:_authToken=", g.secret("opaque"), "\n")


@tpl(S, "airflow_conn_log")
def _(g):
    b, r = g.b, g.r
    b("[", g.ts(" ", False), "] {base.py:", str(r.randint(50, 90)), "} INFO - Using connection to: id: hrms_pg. Host: ", g.pick(*HOSTS), ", Port: 5432, Schema: hrms, Login: ", g.pick(*SVC), ", Password: ", pw(g), ", extra: {}\n")
    b("[", g.ts(" ", False), "] {taskinstance.py:", str(r.randint(1000, 2000)), "} ERROR - Task failed with exception\n")


@tpl(S, "smtp_and_webhook_conf")
def _(g):
    b, r = g.b, g.r
    p = g.person()
    b("smtp.host=smtp.", g.pick("office365.com", "sendgrid.net", "gmail.com"), "\nsmtp.port=587\nsmtp.user=", g.mail(p), "\nsmtp.password=", g.secret("password"), "\nwebhook.hr-alerts=https://hooks.slack.com/services/T", "".join(r.choice("ABCDEFGH0123456789") for _ in range(8)), "/B",
      "".join(r.choice("ABCDEFGH0123456789") for _ in range(8)), "/", g.secret("opaque"), "\n")


@tpl(S, "soap_fault_pii")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    b("<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\"><soapenv:Body><soapenv:Fault><faultcode>Client.Validation</faultcode><faultstring>Invalid national id ", E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR"),
      " for worker ", eid_(p), " (", g.nm(p), ")</faultstring><detail><contact>", g.mail(p), "</contact></detail></soapenv:Fault></soapenv:Body></soapenv:Envelope>")


@tpl(S, "long_config_secret_at_bottom")
def _(g):
    b, r = g.b, g.r
    b("[vendor-attendance-prod]  # NOTE: 'token' in this section is the API token issued by the vendor (treat as secret)\n")
    for _ in range(r.randint(22, 30)):
        b(g.pick("timeout", "retries", "batch_size", "poll_interval", "region", "log_level", "max_conn", "tls_min", "proxy", "cache_ttl"), str(r.randint(0, 9999)), "=", str(r.randint(0, 9999)), "\n")
    b("token=", g.secret("opaque"), "\n")


@tpl(S, "git_remote_and_history")
def _(g):
    b, r = g.b, g.r
    b("$ git remote -v\norigin\thttps://", g.pick("svc-hr", "ci-bot", "deploy"), ":", g.secret("gh_pat"), "@github.com/kestrel/payroll-etl.git (fetch)\n$ git log --oneline -3\n",
      "".join(r.choice("0123456789abcdef") for _ in range(7)), " remove hardcoded creds\n", "".join(r.choice("0123456789abcdef") for _ in range(7)), " add bank file exporter\n")
    b("$ grep -r password config/\nconfig/prod.ini:password = ", g.secret("password"), "\n")


@tpl(S, "card_gateway_debug")
def _(g):
    b, r = g.b, g.r
    p = g.person()
    b(g.ts(" ", False), " DEBUG payment-gw request: {\"card\":\"", E(gen_card(r, 0), "CARD"), "\",\"exp\":\"", f"{r.randint(1, 12):02d}/{r.randint(27, 31)}", "\",\"holder\":\"", g.nm(p, "upper"), "\",\"receipt\":\"", g.mail(p), "\"}\n")
    b(g.ts(" ", False), " DEBUG payment-gw signing with key ", g.secret("stripe"), "\n")


@tpl(S, "upi_callback_log")
def _(g):
    b, r = g.b, g.r
    p, q = g.person(), g.person("external")
    b(g.ts(" ", False), " INFO upi-cb payload=")
    jdump(b, {"txnId": "".join(r.choice(ALNUM) for _ in range(20)), "payerVpa": E(g.v("UPI_ID", p=p), "UPI_ID"), "payeeVpa": E(g.v("UPI_ID", p=q), "UPI_ID"), "status": "SUCCESS", "amount": str(r.randint(10, 9999)) + ".00"}, None)
    b("\n", g.ts(" ", False), " DEBUG signature key=", g.secret("hex40"), "\n")
