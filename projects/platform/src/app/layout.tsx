export const metadata = {
  title: 'نواة — منصة الوكيل الذكي',
  description: 'وكيل ذكاء اصطناعي بحوكمة بشرية — تخطيط، تنفيذ، موافقات، وأثر حي',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ar" dir="rtl">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body
        style={{
          margin: 0,
          fontFamily: "'IBM Plex Sans Arabic', 'Segoe UI', system-ui, sans-serif",
          background: '#f5f4ef',
          color: '#1a1915',
        }}
      >
        {children}
      </body>
    </html>
  );
}
