import 'dotenv/config';
import { type JobContext, ServerOptions, cli, defineAgent, voice } from '@livekit/agents';
import * as openai from '@livekit/agents-plugin-openai';
import * as reson8 from '@reson8-labs/agents-plugin-reson8';
import { fileURLToPath } from 'node:url';

export default defineAgent({
  entry: async (ctx: JobContext) => {
    await ctx.connect();

    const session = new voice.AgentSession({
      stt: new reson8.STT({
        eagerTurnProbability: 0.5,
        finalTurnProbability: 0.7,
      }),
      llm: new openai.LLM(),
      tts: new openai.TTS(),
      turnHandling: {
        turnDetection: 'stt',
        preemptiveGeneration: { enabled: true },
      },
    });

    const agent = new voice.Agent({
      instructions: 'You are a helpful voice assistant.',
    });

    await session.start({ agent, room: ctx.room });
    await session.say('Hallo, hoe kan ik je helpen?');
  },
});

cli.runApp(new ServerOptions({ agent: fileURLToPath(import.meta.url) }));
