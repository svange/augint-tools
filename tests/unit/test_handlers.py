from __future__ import annotations

from types import SimpleNamespace

import requests
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from augint_tools.dashboard.health import FetchContext
from augint_tools.dashboard.health import _handlers as handlers


def _ctx() -> FetchContext:
    return FetchContext(owner="org", repo_name="repo", main_head_sha="abcdef123456")


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code}}, "call")


def test_register_handler_and_all_handlers():
    @handlers.register_handler("unit_test_handler")
    def _tmp(_context: FetchContext, _params: dict) -> tuple[bool, str]:
        return True, "ok"

    assert "unit_test_handler" in handlers.all_handlers()


def test_aws_oidc_trust_policy_scope_prefix_required():
    passed, msg = handlers.aws_oidc_trust_policy_scope(_ctx(), {"expected_subject_prefix": ""})
    assert passed is False
    assert "required" in msg


def test_aws_oidc_trust_policy_scope_aws_errors(monkeypatch):
    iam = SimpleNamespace(get_role=lambda **_: (_ for _ in ()).throw(NoCredentialsError()))
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: iam)
    passed, msg = handlers.aws_oidc_trust_policy_scope(
        _ctx(), {"expected_subject_prefix": "repo:{owner}/{repo_name}:"}
    )
    assert passed is False
    assert "credentials" in msg

    iam = SimpleNamespace(get_role=lambda **_: (_ for _ in ()).throw(_client_error("NoSuchEntity")))
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: iam)
    passed, msg = handlers.aws_oidc_trust_policy_scope(
        _ctx(), {"expected_subject_prefix": "repo:{owner}/{repo_name}:"}
    )
    assert passed is False
    assert "not found" in msg

    class _Boom(BotoCoreError):
        pass

    iam = SimpleNamespace(get_role=lambda **_: (_ for _ in ()).throw(_Boom()))
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: iam)
    passed, msg = handlers.aws_oidc_trust_policy_scope(
        _ctx(), {"expected_subject_prefix": "repo:{owner}/{repo_name}:"}
    )
    assert passed is False
    assert "_Boom" in msg


def test_aws_oidc_trust_policy_scope_doc_and_condition_paths(monkeypatch):
    iam = SimpleNamespace(get_role=lambda **_: {"Role": {"AssumeRolePolicyDocument": "{not-json"}})
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: iam)
    passed, msg = handlers.aws_oidc_trust_policy_scope(
        _ctx(), {"expected_subject_prefix": "repo:{owner}/{repo_name}:"}
    )
    assert passed is False
    assert "not valid JSON" in msg

    iam = SimpleNamespace(
        get_role=lambda **_: {"Role": {"AssumeRolePolicyDocument": {"Statement": [{}]}}}
    )
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: iam)
    passed, msg = handlers.aws_oidc_trust_policy_scope(
        _ctx(), {"expected_subject_prefix": "repo:{owner}/{repo_name}:"}
    )
    assert passed is False
    assert "no OIDC subject condition" in msg

    trust_doc = {
        "Statement": [
            {
                "Condition": {
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": "repo:wrong/repo:ref:refs/heads/main"
                    }
                }
            }
        ]
    }
    iam = SimpleNamespace(get_role=lambda **_: {"Role": {"AssumeRolePolicyDocument": trust_doc}})
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: iam)
    passed, msg = handlers.aws_oidc_trust_policy_scope(
        _ctx(), {"expected_subject_prefix": "repo:{owner}/{repo_name}:"}
    )
    assert passed is False
    assert "outside prefix" in msg

    good_doc = {
        "Statement": [
            {
                "Condition": {
                    "StringEquals": {
                        "token.actions.githubusercontent.com:sub": [
                            "repo:org/repo:ref:refs/heads/main",
                            "repo:org/repo:environment:prod",
                        ]
                    }
                }
            }
        ]
    }
    iam = SimpleNamespace(get_role=lambda **_: {"Role": {"AssumeRolePolicyDocument": good_doc}})
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: iam)
    passed, msg = handlers.aws_oidc_trust_policy_scope(
        _ctx(), {"expected_subject_prefix": "repo:{owner}/{repo_name}:"}
    )
    assert passed is True
    assert "scoped" in msg


def test_http_health_probe_paths(monkeypatch):
    passed, msg = handlers.http_health_probe(_ctx(), {})
    assert passed is False
    assert "url required" in msg

    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.Timeout("slow")),
    )
    passed, msg = handlers.http_health_probe(_ctx(), {"url": "https://example.com"})
    assert passed is False
    assert "timeout" in msg

    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.RequestException("boom")),
    )
    passed, msg = handlers.http_health_probe(_ctx(), {"url": "https://example.com"})
    assert passed is False
    assert "http error" in msg

    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: SimpleNamespace(status_code=200))
    passed, msg = handlers.http_health_probe(
        _ctx(), {"url": "https://{owner}.example.com/{repo_name}", "expected_status": 200}
    )
    assert passed is True
    assert "GET https://org.example.com/repo -> 200" == msg

    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: SimpleNamespace(status_code=503))
    passed, msg = handlers.http_health_probe(
        _ctx(), {"url": "https://example.com", "expected_status": 200}
    )
    assert passed is False
    assert "expected 200" in msg


def test_lambda_deploy_sha_match_paths(monkeypatch):
    ctx = FetchContext(owner="org", repo_name="repo", main_head_sha=None)
    passed, msg = handlers.lambda_deploy_sha_match(ctx, {})
    assert passed is False
    assert "main head SHA not available" in msg

    lam = SimpleNamespace(get_function=lambda **_: (_ for _ in ()).throw(NoCredentialsError()))
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: lam)
    passed, msg = handlers.lambda_deploy_sha_match(_ctx(), {})
    assert passed is False
    assert "credentials" in msg

    lam = SimpleNamespace(
        get_function=lambda **_: (_ for _ in ()).throw(_client_error("ResourceNotFoundException"))
    )
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: lam)
    passed, msg = handlers.lambda_deploy_sha_match(_ctx(), {})
    assert passed is False
    assert "not found" in msg

    lam = SimpleNamespace(get_function=lambda **_: {"Tags": {}})
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: lam)
    passed, msg = handlers.lambda_deploy_sha_match(_ctx(), {})
    assert passed is False
    assert "has no git_sha tag" in msg

    lam = SimpleNamespace(get_function=lambda **_: {"Tags": {"git_sha": "12345678"}})
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: lam)
    passed, msg = handlers.lambda_deploy_sha_match(_ctx(), {})
    assert passed is False
    assert "main is abcdef12" in msg

    lam = SimpleNamespace(get_function=lambda **_: {"Tags": {"git_sha": "abcdef123456"}})
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: lam)
    passed, msg = handlers.lambda_deploy_sha_match(_ctx(), {})
    assert passed is True
    assert "@ abcdef12" in msg
