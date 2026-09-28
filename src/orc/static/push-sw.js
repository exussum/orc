self.addEventListener("push", (event) => {
    const { title, body, tag } = event.data.json();
    event.waitUntil(self.registration.showNotification(title, { body, tag, icon: "/static/icons/android-launchericon-192-192.png" }));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    event.waitUntil(self.clients.openWindow("/log/"));
});
