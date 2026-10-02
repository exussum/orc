self.addEventListener("push", (event) => {
    const { title, body, tag, url } = event.data.json();
    const options = { body, tag, icon: "/static/icons/android-launchericon-192-192.png", data: { url } };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const url = event.notification.data.url || "/log/";
    event.waitUntil(self.clients.openWindow(url).catch(() => self.clients.openWindow("/log/")));
});
