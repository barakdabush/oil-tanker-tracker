import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: "Oil Tanker Monitor — Global Tracking & Analytics",
  description:
    "Real-time worldwide oil tanker tracking, cargo detection, dark fleet monitoring, and supply chain analytics.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={inter.variable}>
      <head>
        <link
          rel="stylesheet"
          href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
          crossOrigin=""
        />
      </head>
      <body>
        <div className="app-container">
          <aside className="sidebar">
            <div className="sidebar-logo">
              <div className="sidebar-logo-icon">🛢️</div>
              <h1>
                Oil Tanker
                <span>Monitor</span>
              </h1>
            </div>

            <nav className="sidebar-nav">
              <Link href="/" className="nav-link" id="nav-dashboard">
                <span className="nav-icon">📊</span>
                <span>Dashboard</span>
              </Link>
              <Link href="/map" className="nav-link" id="nav-map">
                <span className="nav-icon">🗺️</span>
                <span>Live Map</span>
              </Link>
              <Link href="/analytics" className="nav-link" id="nav-analytics">
                <span className="nav-icon">📈</span>
                <span>Analytics</span>
              </Link>
              <Link href="/alerts" className="nav-link" id="nav-alerts">
                <span className="nav-icon">🚨</span>
                <span>Alerts</span>
              </Link>
            </nav>

            <div className="sidebar-footer">
              <div className="sidebar-status">
                <div className="status-dot"></div>
                <span>AIS Stream Active</span>
              </div>
            </div>
          </aside>

          <main className="main-content">{children}</main>
        </div>
      </body>
    </html>
  );
}
