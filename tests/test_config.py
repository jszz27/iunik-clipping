import pytest

from clipping.config import (
    ConfigError,
    load_campaigns,
    load_env_file,
    load_roster,
    load_settings,
)

VALID_CAMPAIGNS = """
campaigns:
  - id: spring_glow_2026
    name: "Spring Glow Launch"
    hashtags: ["IUnikSpringGlow", "이유닉스프링"]
    start: 2026-03-01
    end: 2026-04-30
    priority: 10
  - id: always_on_2026
    hashtags: ["iunik"]
    start: 2026-01-01
    end: 2026-12-31
"""

VALID_ROSTER = """
roster:
  - handle: "@Creator_One"
    note: "Spring Glow, 3-post package"
  - handle: creator_two
  - handle: CREATOR_ONE
"""


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# --- campaigns ---

def test_loads_valid_campaigns(tmp_path):
    campaigns = load_campaigns(write(tmp_path, "campaigns.yaml", VALID_CAMPAIGNS))
    assert [c.id for c in campaigns] == ["spring_glow_2026", "always_on_2026"]

    spring = campaigns[0]
    assert spring.name == "Spring Glow Launch"
    assert spring.priority == 10
    assert str(spring.start) == "2026-03-01"


def test_campaign_hashtags_are_normalised(tmp_path):
    spring = load_campaigns(write(tmp_path, "campaigns.yaml", VALID_CAMPAIGNS))[0]
    # Mixed case in the file, casefolded in memory, so matching is case-insensitive.
    assert spring.hashtags == frozenset({"iunikspringglow", "이유닉스프링"})


def test_campaign_name_defaults_to_id(tmp_path):
    always_on = load_campaigns(write(tmp_path, "campaigns.yaml", VALID_CAMPAIGNS))[1]
    assert always_on.name == "always_on_2026"
    assert always_on.priority == 0


@pytest.mark.parametrize("bad_yaml,expected", [
    ("campaigns:\n  - name: no id here\n    hashtags: [x]\n    start: 2026-01-01\n"
     "    end: 2026-02-01\n", "missing 'id'"),
    ("campaigns:\n  - id: no_tags\n    hashtags: []\n    start: 2026-01-01\n"
     "    end: 2026-02-01\n", "no hashtags"),
    ("campaigns:\n  - id: tags_not_list\n    hashtags: iunik\n    start: 2026-01-01\n"
     "    end: 2026-02-01\n", "must be a list"),
    ("campaigns:\n  - id: backwards\n    hashtags: [x]\n    start: 2026-06-01\n"
     "    end: 2026-01-01\n", "after it ends"),
    ("campaigns:\n  - id: bad_date\n    hashtags: [x]\n    start: 'last March'\n"
     "    end: 2026-02-01\n", "unparseable start"),
    ("campaigns:\n  - id: dup\n    hashtags: [x]\n    start: 2026-01-01\n    end: 2026-02-01\n"
     "  - id: dup\n    hashtags: [y]\n    start: 2026-01-01\n    end: 2026-02-01\n",
     "duplicate campaign id"),
    ("roster:\n  - handle: wrong_top_key\n", "top-level 'campaigns:'"),
    ("campaigns: not_a_list\n", "must be a list"),
])
def test_malformed_campaigns_are_rejected(tmp_path, bad_yaml, expected):
    with pytest.raises(ConfigError, match=expected):
        load_campaigns(write(tmp_path, "campaigns.yaml", bad_yaml))


def test_missing_campaign_file_names_the_path(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_campaigns(tmp_path / "absent.yaml")


def test_invalid_yaml_is_reported_as_such(tmp_path):
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_campaigns(write(tmp_path, "campaigns.yaml", "campaigns: [unclosed\n"))


# --- roster ---

def test_roster_strips_at_sign_casefolds_and_deduplicates(tmp_path):
    roster = load_roster(write(tmp_path, "roster.yaml", VALID_ROSTER))
    assert [r.handle for r in roster] == ["creator_one", "creator_two"]
    assert roster[0].note == "Spring Glow, 3-post package"
    assert roster[1].note == ""


def test_empty_roster_is_allowed(tmp_path):
    assert load_roster(write(tmp_path, "roster.yaml", "roster:\n")) == []


def test_bare_string_roster_entry_suggests_the_fix(tmp_path):
    with pytest.raises(ConfigError, match="handle: someone"):
        load_roster(write(tmp_path, "roster.yaml", "roster:\n  - someone\n"))


def test_roster_entry_without_handle_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="missing 'handle'"):
        load_roster(write(tmp_path, "roster.yaml", "roster:\n  - note: forgot the handle\n"))


# --- settings and .env ---

def test_env_file_parsing_ignores_comments_and_quotes(tmp_path):
    env = write(tmp_path, ".env", '# a comment\n\nA=1\nB="quoted"\nC = spaced \n')
    assert load_env_file(env) == {"A": "1", "B": "quoted", "C": "spaced"}


def test_env_file_rejects_a_line_without_equals(tmp_path):
    with pytest.raises(ConfigError, match="line 2: expected KEY=VALUE"):
        load_env_file(write(tmp_path, ".env", "A=1\njust some prose\n"))


def test_settings_read_keys_and_defaults(tmp_path):
    s = load_settings(tmp_path / "absent.env", environ={"RAPIDAPI_KEYS": "key_a, key_b"})
    assert s.rapidapi_keys == ("key_a", "key_b")
    assert s.brand_handle == "iunik_official"
    assert s.max_calls_per_run == 500
    assert s.db_path == "clipping.db"
    assert s.duplicate_keys_dropped == 0


def test_identical_keys_are_collapsed_and_counted(tmp_path):
    # The real failure this guards: two "different" keys that are the same string give
    # neither extra quota nor failover.
    s = load_settings(tmp_path / "absent.env", environ={"RAPIDAPI_KEYS": "same_key,same_key"})
    assert s.rapidapi_keys == ("same_key",)
    assert s.duplicate_keys_dropped == 1


def test_environment_overrides_the_env_file(tmp_path):
    env = write(tmp_path, ".env", "RAPIDAPI_KEYS=from_file\nBRAND_HANDLE=from_file\n")
    s = load_settings(env, environ={"BRAND_HANDLE": "@FromEnv"})
    assert s.rapidapi_keys == ("from_file",)   # file fills the gap
    assert s.brand_handle == "fromenv"         # environment wins, @ stripped


def test_missing_keys_explains_the_subscription_requirement(tmp_path):
    with pytest.raises(ConfigError, match="active subscription"):
        load_settings(tmp_path / "absent.env", environ={})


@pytest.mark.parametrize("value", ["nope", "0", "-5"])
def test_bad_call_budget_is_rejected(tmp_path, value):
    with pytest.raises(ConfigError, match="MAX_CALLS_PER_RUN"):
        load_settings(
            tmp_path / "absent.env",
            environ={"RAPIDAPI_KEYS": "k", "MAX_CALLS_PER_RUN": value},
        )


def test_optional_sheets_settings_default_to_none(tmp_path):
    s = load_settings(
        tmp_path / "absent.env",
        environ={"RAPIDAPI_KEYS": "k", "GOOGLE_SHEET_ID": ""},
    )
    assert s.google_sheet_id is None
    assert s.google_service_account_json is None
