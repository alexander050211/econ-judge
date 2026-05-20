"""Smoke-test submissions against a running CTFd. Submits sample .dig files
to challenges that historically failed without canonical seeding (chal 2 FA,
chal 3 3-bit adder)."""

import http.cookiejar
import os
import sys

import requests

SAMPLES = {
    2: "tests/samples/5jo-26winter/5조_프로젝트1/A2_전가산기(FullAdder)만들기.dig",
    3: "tests/samples/5jo-26winter/5조_프로젝트1/A3_3비트덧셈연산기만들기.dig",
    15: "tests/samples/5jo-26winter/5조_프로젝트2/A2_2,3비트비교기.dig",
}

cookie_file = os.path.join(os.environ.get("TEMP", "/tmp"), "ctfd.cookies")
jar = http.cookiejar.MozillaCookieJar(cookie_file)
jar.load(ignore_discard=True)
s = requests.Session()
for c in jar:
    s.cookies.set(c.name, c.value, domain=c.domain, path=c.path)

for cid, rel_path in SAMPLES.items():
    abs_path = os.path.abspath(rel_path)
    with open(abs_path, "rb") as f:
        r = s.post(
            f"http://127.0.0.1:4000/api/v1/digital/challenges/{cid}/attempt",
            files={"file": (os.path.basename(abs_path), f, "application/octet-stream")},
        )
    print(f"chal {cid}: HTTP {r.status_code}")
    print(f"  {r.json()}")
