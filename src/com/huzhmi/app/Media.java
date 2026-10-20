package com.huzhmi.app;

final class Media {
    String title;
    String author;
    String url;
    String referer;
    String cookie;

    Media(String title, String author, String url, String referer, String cookie) {
        this.title = title;
        this.author = author;
        this.url = url;
        this.referer = referer;
        this.cookie = cookie;
    }
}
