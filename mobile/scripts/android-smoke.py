"""Drive real Android native views without credentials or provider inference."""
import re
import subprocess
import time
import xml.etree.ElementTree as ET

PACKAGE = "com.agentcommandcenter.acc"

def adb(*args):
    return subprocess.check_output(["adb", *args], text=True, stderr=subprocess.STDOUT)

def hierarchy():
    adb("shell", "uiautomator", "dump", "/sdcard/acc-smoke.xml")
    return ET.fromstring(adb("shell", "cat", "/sdcard/acc-smoke.xml"))

def healthy():
    try:
        assert adb("shell", "pidof", PACKAGE).strip()
        top = adb("shell", "dumpsys", "activity", "activities")
        assert any(PACKAGE in line for line in top.splitlines() if "mResumedActivity" in line or "topResumedActivity" in line)
    except (subprocess.CalledProcessError, AssertionError):
        # Only exception/React stack buffers; never ActivityManager intent URLs.
        crash = adb("logcat", "-b", "crash", "-d", "-t", "120")
        print(crash)
        raise AssertionError("ACC left the foreground or its process terminated")

def tap(label):
    end = time.monotonic() + 30
    while time.monotonic() < end:
        healthy()
        tree = hierarchy()
        candidates = [n for n in tree.iter("node") if n.get("text") == label or n.get("content-desc") == label]
        if candidates:
            n = candidates[0]
            bounds = list(map(int, re.findall(r"\d+", n.get("bounds", ""))))
            assert len(bounds) == 4, label
            adb("shell", "input", "tap", str((bounds[0] + bounds[2]) // 2), str((bounds[1] + bounds[3]) // 2))
            time.sleep(1)
            return
        time.sleep(.5)
    raise AssertionError("UI control not found: " + label)

def expect(label):
    healthy()
    tree = hierarchy()
    assert any(label in n.get("text", "") or label in n.get("content-desc", "") for n in tree.iter("node")), label

adb("logcat", "-c")
adb("shell", "am", "start", "-W", "-n", PACKAGE + "/.MainActivity")
time.sleep(5)
try:
    # The precise owner-reported trigger, from the Claude tab.
    tap("Claude Code")
    tap("Set up connections")
    expect("Provider connections")
    print("PASS: Claude Set up connections opens native panel")
    tap("Close")
    tap("ChatGPT")
    tap("Connect ChatGPT")
    expect("Provider connections")
    print("PASS: ChatGPT Connect ChatGPT opens native panel")
    tap("Close")
    for _ in range(3):
        tap("Connections")
        expect("Provider connections")
        tap("Close")
    print("PASS: native Connections opens/closes repeatedly")
except Exception:
    print(adb("logcat", "-b", "crash", "-d", "-t", "120"))
    raise
