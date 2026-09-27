self.addEventListener("push", (event) => {
    const { title, body } = event.data.json();
    event.waitUntil(self.registration.showNotification(title, { body, icon: "/static/icons/android-launchericon-192-192.png" }));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    event.waitUntil(self.clients.openWindow("/log/"));
});
