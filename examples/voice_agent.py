from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli

from livekit.plugins import openai, reson8

load_dotenv()


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions="Je bent een behulpzame assistent.")

    async def on_enter(self) -> None:
        self.session.generate_reply(instructions="Begroet de gebruiker en bied je hulp aan.")


async def entrypoint(ctx: JobContext):
    session = AgentSession(
        stt=reson8.STT(),
        llm=openai.LLM(),
        tts=openai.TTS(),
        # "stt" hands turn detection to Reson8 and lets the agent start
        # generating on our preflight transcript instead of the confirmation.
        turn_handling={
            "turn_detection": "stt",
            "preemptive_generation": {"enabled": True},
        },
    )
    await session.start(agent=Assistant(), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
