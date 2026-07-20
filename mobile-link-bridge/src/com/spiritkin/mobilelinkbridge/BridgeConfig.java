package com.spiritkin.mobilelinkbridge;

final class BridgeConfig {
    static final String DEFAULT_RECEIVER_URL = "http://100.83.63.91:8791/android/link";
    static final int DEFAULT_RECEIVER_PORT = 8791;
    static final String[] FALLBACK_RECEIVER_URLS = {
            "http://100.83.63.91:8791/android/link",
            "http://192.168.1.2:8791/android/link"
    };

    private BridgeConfig() {
    }
}
