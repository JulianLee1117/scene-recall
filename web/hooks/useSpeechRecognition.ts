"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type SpeechStatus =
  | "idle"
  | "requesting"
  | "listening"
  | "processing";

interface SpeechRecognitionEventLike extends Event {
  readonly resultIndex: number;
  readonly results: SpeechRecognitionResultList;
}

interface SpeechRecognitionErrorEventLike extends Event {
  readonly error: string;
  readonly message: string;
}

interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  maxAlternatives: number;
  onstart: (() => void) | null;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

type SpeechWindow = Window & {
  SpeechRecognition?: SpeechRecognitionConstructor;
  webkitSpeechRecognition?: SpeechRecognitionConstructor;
};

interface UseSpeechRecognitionOptions {
  onTranscript: (transcript: string) => void;
  onComplete: (transcript: string) => void;
}

function recognitionConstructor(): SpeechRecognitionConstructor | null {
  if (typeof window === "undefined") return null;
  const speechWindow = window as SpeechWindow;
  return (
    speechWindow.SpeechRecognition ??
    speechWindow.webkitSpeechRecognition ??
    null
  );
}

function readableSpeechError(code: string): string | null {
  switch (code) {
    case "aborted":
      return null;
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone access is blocked. Allow it in your browser to use voice search.";
    case "audio-capture":
      return "No microphone was found.";
    case "no-speech":
      return "I didn’t hear anything. Try again when you’re ready.";
    case "network":
      return "Speech recognition is temporarily unavailable.";
    case "language-not-supported":
      return "Voice search isn’t available for your browser language.";
    default:
      return "Voice search couldn’t start. Please try again.";
  }
}

export function useSpeechRecognition({
  onTranscript,
  onComplete,
}: UseSpeechRecognitionOptions) {
  const [isSupported, setIsSupported] = useState(false);
  const [status, setStatus] = useState<SpeechStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const transcriptRef = useRef("");
  const originalValueRef = useRef("");
  const failedRef = useRef(false);
  const onTranscriptRef = useRef(onTranscript);
  const onCompleteRef = useRef(onComplete);

  useEffect(() => {
    onTranscriptRef.current = onTranscript;
    onCompleteRef.current = onComplete;
  }, [onComplete, onTranscript]);

  useEffect(() => {
    setIsSupported(recognitionConstructor() !== null);
    return () => {
      const recognition = recognitionRef.current;
      recognitionRef.current = null;
      recognition?.abort();
    };
  }, []);

  const clearError = useCallback(() => setError(null), []);

  const cancel = useCallback(() => {
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    recognition?.abort();
    setStatus("idle");
  }, []);

  const stop = useCallback(() => {
    if (!recognitionRef.current || status !== "listening") return;
    setStatus("processing");
    recognitionRef.current.stop();
  }, [status]);

  const start = useCallback((originalValue: string) => {
    const Constructor = recognitionConstructor();
    if (!Constructor) {
      setIsSupported(false);
      return;
    }
    if (recognitionRef.current) return;

    const recognition = new Constructor();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = navigator.language || "en-US";
    recognition.maxAlternatives = 1;

    transcriptRef.current = "";
    originalValueRef.current = originalValue;
    failedRef.current = false;
    setError(null);
    setStatus("requesting");

    recognition.onstart = () => {
      if (recognitionRef.current !== recognition) return;
      setStatus("listening");
      onTranscriptRef.current("");
    };

    recognition.onresult = (event) => {
      if (recognitionRef.current !== recognition) return;
      const transcriptParts: string[] = [];
      for (let index = 0; index < event.results.length; index += 1) {
        const alternative = event.results[index][0];
        if (alternative?.transcript) {
          transcriptParts.push(alternative.transcript);
        }
      }
      const transcript = transcriptParts.join(" ").replace(/\s+/g, " ").trim();
      transcriptRef.current = transcript;
      onTranscriptRef.current(transcript);
    };

    recognition.onerror = (event) => {
      if (recognitionRef.current !== recognition) return;
      failedRef.current = true;
      setStatus("idle");
      const message = readableSpeechError(event.error);
      if (message) {
        setError(message);
        onTranscriptRef.current(originalValueRef.current);
      }
    };

    recognition.onend = () => {
      if (recognitionRef.current !== recognition) return;
      recognitionRef.current = null;
      setStatus("idle");
      const transcript = transcriptRef.current.trim();
      if (!failedRef.current && transcript) {
        onTranscriptRef.current(transcript);
        onCompleteRef.current(transcript);
      } else if (!failedRef.current) {
        onTranscriptRef.current(originalValueRef.current);
        setError("I didn’t hear anything. Try again when you’re ready.");
      }
    };

    recognitionRef.current = recognition;
    try {
      recognition.start();
    } catch {
      recognitionRef.current = null;
      failedRef.current = true;
      setStatus("idle");
      setError("Voice search couldn’t start. Please try again.");
    }
  }, []);

  const toggle = useCallback(
    (originalValue: string) => {
      if (status === "listening") {
        stop();
      } else if (status === "requesting") {
        cancel();
      } else if (status === "idle") {
        start(originalValue);
      }
    },
    [cancel, start, status, stop]
  );

  return {
    isSupported,
    status,
    error,
    clearError,
    cancel,
    toggle,
  };
}
