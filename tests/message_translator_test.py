from __future__ import annotations

from message_translator import (
    SlackMessageRef,
    _p_to_ts,
    extract_slack_message_urls,
    is_stop_command,
    translate_slack_message,
)


def test_translate_slack_markup() -> None:
    translated = translate_slack_message(
        "<@U123> check <https://example.com|example> in <#C123|general>",
        user_lookup={"U123": "alice"},
    )
    assert translated == "@alice check https://example.com in #general"


def test_translate_without_lookup_falls_back_to_user_id() -> None:
    translated = translate_slack_message("hello <@U999>")
    assert translated == "hello @U999"


def test_smart_quotes_normalized_to_ascii() -> None:
    translated = translate_slack_message('!git commit -am \u201cfix sentry feedback\u201d')
    assert translated == '!git commit -am "fix sentry feedback"'


def test_smart_single_quotes_normalized_to_ascii() -> None:
    translated = translate_slack_message("!git commit -am \u2018fix sentry feedback\u2019")
    assert translated == "!git commit -am 'fix sentry feedback'"


def test_stop_detection() -> None:
    assert is_stop_command("stop")
    assert is_stop_command(" EXIT ")
    assert not is_stop_command("stopping")


def test_p_to_ts_converts_full_timestamp() -> None:
    assert _p_to_ts("1772824099371809") == "1772824099.371809"


def test_p_to_ts_short_timestamp_unchanged() -> None:
    assert _p_to_ts("1772824099") == "1772824099"


def test_extract_slack_message_url_basic() -> None:
    text = "Check https://myteam.slack.com/archives/C0AH7FL4YB0/p1772824099371809 please"
    refs = extract_slack_message_urls(text)
    assert len(refs) == 1
    assert refs[0].channel_id == "C0AH7FL4YB0"
    assert refs[0].message_ts == "1772824099.371809"
    assert refs[0].thread_ts is None


def test_extract_slack_message_url_with_thread_ts() -> None:
    text = (
        "see https://myteam.slack.com/archives/C0AH7FL4YB0/p1772824099371809"
        "?thread_ts=1772824000.000001&cid=C0AH7FL4YB0"
    )
    refs = extract_slack_message_urls(text)
    assert len(refs) == 1
    assert refs[0].channel_id == "C0AH7FL4YB0"
    assert refs[0].message_ts == "1772824099.371809"
    assert refs[0].thread_ts == "1772824000.000001"


def test_extract_slack_message_url_multiple() -> None:
    text = (
        "compare https://ws.slack.com/archives/C111/p1000000000000001 "
        "with https://ws.slack.com/archives/C222/p2000000000000002"
    )
    refs = extract_slack_message_urls(text)
    assert len(refs) == 2
    assert refs[0].channel_id == "C111"
    assert refs[0].message_ts == "1000000000.000001"
    assert refs[1].channel_id == "C222"
    assert refs[1].message_ts == "2000000000.000002"


def test_extract_slack_message_url_no_match() -> None:
    assert extract_slack_message_urls("no links here") == []
    assert extract_slack_message_urls("https://google.com") == []


def test_extract_slack_message_url_preserves_full_url() -> None:
    url = "https://team.slack.com/archives/C0AH7FL4YB0/p1772824099371809?thread_ts=1.2&cid=C0AH7FL4YB0"
    refs = extract_slack_message_urls(f"look at {url} thanks")
    assert len(refs) == 1
    assert refs[0].url == url
