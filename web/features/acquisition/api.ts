const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

export class AcquisitionError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function acquisitionRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_URL}/acquisition${path}`, { ...init, headers, cache: "no-store" });
  if (!response.ok) {
    let message = `The film queue could not complete this request (${response.status}).`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string" && body.detail.trim()) message = body.detail;
      else if (typeof body.message === "string" && body.message.trim()) message = body.message;
      else if (Array.isArray(body.detail)) {
        const details = body.detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join(". ");
        if (details) message = details;
      }
    } catch { /* A proxy may return a non-JSON error. */ }
    throw new AcquisitionError(message, response.status);
  }
  return response.json();
}

export function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "The film queue is temporarily unavailable.";
}
