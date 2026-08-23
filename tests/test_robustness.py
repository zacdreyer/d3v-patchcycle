"""Parser robustness and performance sanity (Phase 7 hardening).

Failure-injection principle: malformed/unexpected external input must never
crash a parser; it yields empty/partial results or a clean validation error.
Performance principle: 1000+ package lists must be handled without drama.
"""

from __future__ import annotations

import random
import string
import time

import pytest

from patchcycle.errors import ConfigError
from patchcycle.osdetect import parse_os_release
from patchcycle.providers.apt import parse_policy_security, parse_simulation


def fuzz_bytes(seed: int, length: int) -> str:
    # S311: deterministic seeded fuzzing for parser robustness — not crypto.
    rng = random.Random(seed)  # noqa: S311
    alphabet = string.printable + "\x00\xff\t\r\né"
    return "".join(rng.choice(alphabet) for _ in range(length))


class TestOsReleaseFuzz:
    @pytest.mark.parametrize("seed", range(25))
    def test_never_crashes(self, seed):
        result = parse_os_release(fuzz_bytes(seed, 500))
        assert isinstance(result, dict)
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in result.items())

    def test_deeply_nested_quotes(self):
        # os-release(5): concatenation of quoted strings is not supported;
        # first/last quote delimit, backslash escapes are shell-style.
        text = 'A="""\nB="a\\"b\\\\c"\nC=\'it\'\'s\'\n'
        result = parse_os_release(text)
        assert result["A"] == '"'  # '""' content between outer quotes
        assert result["B"] == 'a"b\\c'
        assert result["C"] == "it''s"  # single quotes: no escape processing


class TestAptParsersFuzz:
    @pytest.mark.parametrize("seed", range(25))
    def test_simulation_never_crashes(self, seed):
        result = parse_simulation(fuzz_bytes(seed + 1000, 800))
        assert isinstance(result, list)

    @pytest.mark.parametrize("seed", range(15))
    def test_policy_never_crashes(self, seed):
        result = parse_policy_security(fuzz_bytes(seed + 2000, 600))
        assert isinstance(result, set)


class TestConfigRobustness:
    def test_huge_unknown_section_rejected_fast(self):
        big = {"x" * 1000: {"y": "z"}}
        with pytest.raises(ConfigError):
            from patchcycle.config import parse_config

            parse_config(big)

    def test_deeply_nested_values_rejected(self):
        nested: dict = {}
        cursor = nested
        for _ in range(500):
            cursor["a"] = {}
            cursor = cursor["a"]
        with pytest.raises((ConfigError, RecursionError)):
            from patchcycle.config import parse_config

            parse_config({"maintenance": nested})


class TestPerformance:
    def test_1000_package_simulation_parses_fast(self):
        lines = []
        for i in range(1000):
            lines.append(f"Inst pkg{i} [1.0.{i}] (1.1.{i} Ubuntu:24.04/noble-updates [amd64])")
        lines.append("1000 upgraded, 0 newly installed, 0 to remove.")
        output = "\n".join(lines)
        start = time.monotonic()
        updates = parse_simulation(output)
        elapsed = time.monotonic() - start
        assert len(updates) == 1000
        assert elapsed < 0.5  # linear parse; generous bound

    def test_1000_package_policy_classification_fast(self):
        blocks = []
        for i in range(1000):
            pocket = "noble-security" if i % 2 == 0 else "noble-updates"
            blocks.append(
                f"pkg{i}:\n  Installed: 1.0\n  Candidate: 1.1\n  Version table:\n"
                f"     1.1 500\n        500 http://x/ubuntu {pocket}/main amd64 Packages\n"
            )
        output = "\n".join(blocks)
        start = time.monotonic()
        security = parse_policy_security(output)
        elapsed = time.monotonic() - start
        assert len(security) == 500
        assert elapsed < 1.0
