import { type JobContext, WorkerOptions, cli, defineAgent, voice } from '@livekit/agents';
import * as openai from '@livekit/agents-plugin-openai';
import * as reson8 from '@reson8/agents-plugin-reson8';
import { fileURLToPath } from 'node:url';

export default defineAgent({
  entry: async (ctx: JobContext) => {
    await ctx.connect();

    // reson8.STT streams with server-side turn detection, which keeps
    // voice-agent responses snappy. Any language is supported: omit `language`
    // to auto-detect, or pass any code (e.g. { language: 'nl' }) to pin it.
    const session = new voice.AgentSession({
      stt: new reson8.STT(),
      llm: new openai.LLM(),
      tts: new openai.TTS(),
    });

    const agent = new voice.Agent({
      instructions: 'You are a helpful voice assistant.',
    });

    await session.start({ agent, room: ctx.room });
    await session.say('Hallo, hoe kan ik je helpen?');
  },
});

cli.runApp(new WorkerOptions({ agent: fileURLToPath(import.meta.url) }));
