/** Minimal SSE parser over a ReadableStream.
 *
 * EventSource cannot be used here: it is GET-only and cannot carry a JSON body,
 * so the ask endpoint has to be read as a stream by hand.
 */
export interface SseEvent {
  event: string;
  data: any; // eslint-disable-line @typescript-eslint/no-explicit-any
}

export async function* parseSSE(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      if (signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Frames are separated by a blank line. A partial frame stays in the
      // buffer until the rest arrives.
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);

        let event = "message";
        const dataLines: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith(":")) continue; // keepalive comment
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        }
        if (!dataLines.length) continue;
        try {
          yield { event, data: JSON.parse(dataLines.join("\n")) };
        } catch {
          // A malformed frame must not kill the stream.
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
