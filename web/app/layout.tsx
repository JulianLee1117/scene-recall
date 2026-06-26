import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "scene-recall",
  description: "Cinema semantic search",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
