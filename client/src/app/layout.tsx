import type { Metadata } from "next";
import { Fraunces, Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

// Editorial display face, used for headings only — body stays sans.
const displaySerif = Fraunces({
  variable: "--font-display-serif",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "Scraping Hub — Bid Intelligence",
    template: "%s · Scraping Hub",
  },
  description:
    "Scraping Hub monitors public procurement portals, collects every matching solicitation and its documents, and exports them to a spreadsheet.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${displaySerif.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-paper text-ink-900" suppressHydrationWarning>
        {children}
      </body>
    </html>
  );
}
