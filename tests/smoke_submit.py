"""Submit pin-compatible reference circuits to a local summer-set server."""

import http.cookiejar
import os
import sys

import requests

SAMPLES = {
    3: "tests/samples/5jo-26winter/5조_연습문제/2번_입력이3개인AND게이트.dig",
    4: "tests/samples/5jo-26winter/5조_연습문제/3번_2대1멀티플렉서(MUX).dig",
    7: "tests/samples/5jo-26winter/5조_프로젝트1/A1_반가산기(HalfAdder)만들기.dig",
    10: "tests/samples/5jo-26winter/5조_미션문제/4번_21세기윤년판독기만들기.dig",
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
