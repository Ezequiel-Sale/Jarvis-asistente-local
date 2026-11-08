"""Un 500 de Ollama (su motor se cayó al cargar el modelo) se reintenta una vez."""

import unittest
from unittest.mock import Mock, patch

import requests

import jarvis_agent


def _respuesta(codigo: int) -> Mock:
    r = Mock(status_code=codigo)
    if codigo >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(f"{codigo} Server Error")
    return r


class RetryTests(unittest.TestCase):
    @patch.object(jarvis_agent.time, "sleep")
    @patch.object(jarvis_agent.requests, "post")
    def test_retries_once_after_server_error(self, post, dormir) -> None:
        post.side_effect = [_respuesta(500), _respuesta(200)]
        self.assertEqual(jarvis_agent._post_con_reintento("u", {}, stream=False).status_code, 200)
        self.assertEqual(post.call_count, 2)
        dormir.assert_called_once()

    @patch.object(jarvis_agent.time, "sleep")
    @patch.object(jarvis_agent.requests, "post")
    def test_gives_up_after_second_error(self, post, _dormir) -> None:
        post.side_effect = [_respuesta(500), _respuesta(500)]
        with self.assertRaises(requests.HTTPError):
            jarvis_agent._post_con_reintento("u", {}, stream=False)

    @patch.object(jarvis_agent.time, "sleep")
    @patch.object(jarvis_agent.requests, "post")
    def test_client_errors_are_not_retried(self, post, dormir) -> None:
        post.side_effect = [_respuesta(400)]
        with self.assertRaises(requests.HTTPError):
            jarvis_agent._post_con_reintento("u", {}, stream=False)
        dormir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
