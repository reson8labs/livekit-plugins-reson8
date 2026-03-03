import asyncio

from dotenv import load_dotenv
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli
from livekit.agents.voice import VoiceAgent
from livekit.plugins import openai, reson8

load_dotenv()


async def entrypoint(ctx: JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    agent = VoiceAgent(
        stt=reson8.STT(language="nl"),
        llm=openai.LLM(),
        tts=openai.TTS(),
    )
    agent.start(ctx.room)

    await agent.say("Hallo, hoe kan ik je helpen?")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
