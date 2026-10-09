self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
    const { title, body, tag, url } = event.data.json();
    const options = { body, tag, icon: "/static/icons/android-launchericon-192-192.png", data: { url } };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const link = event.notification.data.url;
    const mac = /Macintosh/.test(navigator.userAgent) && link.startsWith("https://meet.google.com/");
    const url = !link ? "/log/" : mac ? `/api/push/open?url=${encodeURIComponent(link)}` : link;
    event.waitUntil(self.clients.openWindow(url).catch((error) => self.clients.openWindow(`/log/?link=${encodeURIComponent(url)}&error=${encodeURIComponent(error.message)}`)));
});
