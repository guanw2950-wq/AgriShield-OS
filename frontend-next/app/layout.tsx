import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgriShield Growth Console",
  description: "NDVI crop-growth analysis console for AgriShield OS"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
