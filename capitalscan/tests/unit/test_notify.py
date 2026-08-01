"""Notifier protocol (ADR 054): each channel is active only when its
required env vars are present, and a failing channel never blocks another.
"""

from __future__ import annotations

import pytest

from capitalscan.jobs import notify

NOTIFY_VARS = [
    "NOTIFY_SMTP_HOST",
    "NOTIFY_SMTP_USER",
    "NOTIFY_SMTP_PASS",
    "NOTIFY_SMTP_TO",
    "NOTIFY_DISCORD_WEBHOOK",
    "NOTIFY_NTFY_TOPIC",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in NOTIFY_VARS:
        monkeypatch.delenv(var, raising=False)
    # `active_notifiers` calls `_load_env`, which loads .env / .env.local off
    # disk — point it at a directory with neither so ambient files on the
    # developer's machine can't leak into the test.
    monkeypatch.setattr(notify, "_load_env", lambda: None)


def test_no_channels_active_with_no_env_vars_set():
    assert notify.active_notifiers() == []


def test_discord_active_when_webhook_set(monkeypatch):
    monkeypatch.setenv("NOTIFY_DISCORD_WEBHOOK", "https://discord.example/webhook")
    notifiers = notify.active_notifiers()
    assert [n.name for n in notifiers] == ["discord"]


def test_ntfy_active_when_topic_set(monkeypatch):
    monkeypatch.setenv("NOTIFY_NTFY_TOPIC", "capitalscan-alerts")
    notifiers = notify.active_notifiers()
    assert [n.name for n in notifiers] == ["ntfy"]


def test_smtp_requires_all_four_vars(monkeypatch):
    monkeypatch.setenv("NOTIFY_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("NOTIFY_SMTP_USER", "user")
    # password/to missing -> smtp stays inactive
    assert notify.active_notifiers() == []
    monkeypatch.setenv("NOTIFY_SMTP_PASS", "secret")
    monkeypatch.setenv("NOTIFY_SMTP_TO", "me@example.com")
    notifiers = notify.active_notifiers()
    assert [n.name for n in notifiers] == ["smtp"]


def test_multiple_channels_active_simultaneously(monkeypatch):
    monkeypatch.setenv("NOTIFY_DISCORD_WEBHOOK", "https://discord.example/webhook")
    monkeypatch.setenv("NOTIFY_NTFY_TOPIC", "capitalscan-alerts")
    notifiers = notify.active_notifiers()
    assert sorted(n.name for n in notifiers) == ["discord", "ntfy"]


class _FailingNotifier:
    name = "failing"

    def send(self, subject, body):
        raise RuntimeError("channel is down")


class _WorkingNotifier:
    name = "working"

    def send(self, subject, body):
        return True


def test_notify_all_skips_a_failing_channel_without_raising():
    sent = notify.notify_all([_FailingNotifier(), _WorkingNotifier()], "subj", "body")
    assert sent == ["working"]


def test_notify_all_returns_empty_list_when_every_channel_fails():
    sent = notify.notify_all([_FailingNotifier()], "subj", "body")
    assert sent == []
