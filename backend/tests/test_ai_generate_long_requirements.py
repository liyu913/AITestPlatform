import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.ai import ai_generate
from app.config import settings
from app.main import app
from app.schemas.ai.ai_gen import AiGenerateRequest
from tests.conftest import TestingSessionLocal
from tests.test_ai_generate import _login, _project, _user


@pytest.mark.parametrize('mode', ['fast', 'agent'])
@pytest.mark.parametrize('streaming', [False, True])
def test_long_requirement_reaches_model_without_truncation(monkeypatch, mode, streaming):
    monkeypatch.setattr(settings, 'llm_api_key', 'sk-test')
    monkeypatch.setattr(ai_generate, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr('app.api.functional.requirements.enqueue_reindex', Mock())
    cases = json.dumps({'cases': [{'title': 'Long document case', 'overall_expected': 'Requirement satisfied',
                                 'steps': [{'order': 0, 'action': 'Check requirement tail', 'expected': 'Tail retained'}]}]})
    complete = Mock(return_value=cases)
    if mode == 'agent':
        complete.side_effect = [
            '{"is_clear":true,"summary":"Long requirement","ambiguities":[],"questions":[]}',
            '{"test_points":[{"id":"TP-1","title":"Check tail","category":"功能","priority_hint":"P1"}]}',
        ]
    stream = Mock(side_effect=lambda *a, **kw: iter([cases]))
    monkeypatch.setattr(ai_generate.llm, 'chat_complete', complete)
    monkeypatch.setattr(ai_generate.llm, 'chat_complete_stream', stream)
    username = f'long_{mode}_{streaming}'
    _user(username, 'test-password')
    token = _login(username, 'test-password')
    pid = _project(token)
    client = TestClient(app)
    headers = {'Authorization': f'Bearer {token}'}
    document = 'BEGIN_DOCUMENT_' + ('x' * 19096) + '_END_DOCUMENT_SENTINEL'
    response = client.post(f'/api/v1/projects/{pid}/requirements/', headers=headers,
                           json={'title': 'Long requirement', 'body_md': document})
    assert response.status_code == 201
    path = f'/api/v1/projects/{pid}/ai/generate-testcases' + ('/stream' if streaming else '')
    response = client.post(path, headers=headers, json={
        'requirement_id': response.json()['id'], 'messages': [], 'generate_mode': mode,
    })
    assert response.status_code == 200
    if streaming:
        event = 'await_test_points_confirm' if mode == 'agent' else 'done'
        assert f'"type": "{event}"' in response.text
        assert '"type": "error"' not in response.text
    elif mode == 'fast':
        assert response.json()['cases'][0]['title'] == 'Long document case'
    else:
        assert response.json()['assistant_message']
    called = stream if streaming and mode == 'fast' else complete
    assert called.called
    prompt = '\n'.join(m['content'] for m in called.call_args_list[0].args[0])
    assert document in prompt
    assert '_END_DOCUMENT_SENTINEL' in prompt


def test_user_chat_limit_is_preserved():
    valid = AiGenerateRequest(messages=[{'role': 'user', 'content': 'x' * 16000}])
    assert len(valid.messages[0].content) == 16000
    with pytest.raises(ValidationError):
        AiGenerateRequest(messages=[{'role': 'user', 'content': 'x' * 16001}])
