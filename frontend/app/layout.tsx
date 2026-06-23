import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "MiniMax Agent Workflow",
  description: "Evidence-driven multi-agent task console",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
