"""A litellm response stub — just enough for the two attributes the gateway reads.

Lives here rather than inside one test module because two files need it:
`tests/test_gateway.py` (recording behaviour) and `tests/test_llm_telemetry.py`
(the gateway → telemetry → database seam, spec §7 criterion 3).
"""


class FakeUsage:
    def __init__(self, prompt: int, completion: int):
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class FakeMessage:
    def __init__(self, content: str):
        self.content = content


class FakeChoice:
    def __init__(self, content: str):
        self.message = FakeMessage(content)


class FakeResponse:
    """The gateway touches exactly `.choices[0].message.content` and `.usage`."""

    def __init__(self, content: str, prompt: int, completion: int):
        self.choices = [FakeChoice(content)]
        self.usage = FakeUsage(prompt, completion)


def ok_response(content: str, *, prompt: int, completion: int) -> FakeResponse:
    return FakeResponse(content, prompt, completion)
