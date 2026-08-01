"""Notifier protocol: SMTP, Discord webhook, ntfy (ADR 054, BUILD §8.3).

Each implementation reads its own credentials from the environment
(`.env.example`'s `NOTIFY_*` vars) and is "active" purely by their presence —
there is no separate on/off flag, so any combination of channels can be live
at once. This module owns the IO `core/` may not perform (invariant 1).
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import requests

from capitalscan.jobs.db import _load_env


class Notifier(Protocol):
    name: str

    def send(self, subject: str, body: str) -> bool:
        """Deliver one message. Returns True on success, False on failure —
        never raises, so one channel's outage cannot take down the others.
        """
        ...


@dataclass
class SmtpNotifier:
    host: str
    user: str
    password: str
    to: str
    name: str = "smtp"

    def send(self, subject: str, body: str) -> bool:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = self.user
            msg["To"] = self.to
            msg.set_content(body)
            with smtplib.SMTP_SSL(self.host) as server:
                server.login(self.user, self.password)
                server.send_message(msg)
            return True
        except Exception:
            return False

    @classmethod
    def from_env(cls, env: dict) -> "SmtpNotifier | None":
        host = env.get("NOTIFY_SMTP_HOST")
        user = env.get("NOTIFY_SMTP_USER")
        password = env.get("NOTIFY_SMTP_PASS")
        to = env.get("NOTIFY_SMTP_TO")
        if not (host and user and password and to):
            return None
        return cls(host=host, user=user, password=password, to=to)


@dataclass
class DiscordNotifier:
    webhook_url: str
    name: str = "discord"

    def send(self, subject: str, body: str) -> bool:
        try:
            resp = requests.post(
                self.webhook_url, json={"content": f"**{subject}**\n{body}"}, timeout=10
            )
            return resp.ok
        except requests.RequestException:
            return False

    @classmethod
    def from_env(cls, env: dict) -> "DiscordNotifier | None":
        url = env.get("NOTIFY_DISCORD_WEBHOOK")
        return cls(webhook_url=url) if url else None


@dataclass
class NtfyNotifier:
    topic: str
    name: str = "ntfy"
    base_url: str = "https://ntfy.sh"

    def send(self, subject: str, body: str) -> bool:
        try:
            resp = requests.post(
                f"{self.base_url}/{self.topic}",
                data=body.encode("utf-8"),
                headers={"Title": subject},
                timeout=10,
            )
            return resp.ok
        except requests.RequestException:
            return False

    @classmethod
    def from_env(cls, env: dict) -> "NtfyNotifier | None":
        topic = env.get("NOTIFY_NTFY_TOPIC")
        return cls(topic=topic) if topic else None


def active_notifiers() -> list[Notifier]:
    """Every channel whose required env vars are set, in a fixed order."""
    import os

    _load_env()
    env = dict(os.environ)
    candidates = [
        SmtpNotifier.from_env(env),
        DiscordNotifier.from_env(env),
        NtfyNotifier.from_env(env),
    ]
    return [n for n in candidates if n is not None]


def notify_all(notifiers: list[Notifier], subject: str, body: str) -> list[str]:
    """Send to every notifier, independently. Returns the channel names that
    succeeded — this becomes `signal_reports.channels_sent`. A channel that
    raises or returns False is skipped, never lets one outage block another.
    """
    sent = []
    for notifier in notifiers:
        try:
            if notifier.send(subject, body):
                sent.append(notifier.name)
        except Exception:
            continue
    return sent
