/* Spike step 5: network probe for ESP32 / ESP32-S3 under Espressif QEMU (open_eth).
 *
 * Licence zone: firmware (compiled into firmware) -> permissive, MIT/Apache-2.0 to be decided.
 * Built on the structure of ESP-IDF's http_request and tcp_server examples
 * (Public Domain / CC0).
 *
 * What it does once the network is up (all output on UART0, lines start with "PROBE"):
 *   1. HTTP GET http://example.com/       (DHCP + DNS + internet, through QEMU's slirp NAT)
 *   2. HTTP GET http://10.0.2.2:18071/    (a server on the host; slirp maps 10.0.2.2 to the host's 127.0.0.1)
 *   3. TCP servers for inbound tests (reached from the host with -nic user,hostfwd=...):
 *        3333 echo   (round-trip latency)
 *        3334 sink   (host -> chip throughput; reads until the peer closes)
 *        3335 source (chip -> host throughput; sends SOURCE_BYTES then closes)
 */
#include <string.h>
#include <errno.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "protocol_examples_common.h"
#include "lwip/sockets.h"
#include "lwip/netdb.h"

#define HOST_SERVER_IP   "10.0.2.2"
#define HOST_SERVER_PORT "18071"
#define SOURCE_BYTES     (4 * 1024 * 1024)

static const char *TAG = "net_probe";

static int64_t now_ms(void) { return esp_timer_get_time() / 1000; }

static void http_get(const char *host, const char *port, const char *path)
{
    const struct addrinfo hints = { .ai_family = AF_INET, .ai_socktype = SOCK_STREAM };
    struct addrinfo *res = NULL;
    char buf[512];
    int64_t t0 = now_ms();
    int err = getaddrinfo(host, port, &hints, &res);
    if (err != 0 || res == NULL) {
        printf("PROBE GET %s: DNS FAIL err=%d\n", host, err);
        return;
    }
    char ip[16];
    inet_ntoa_r(((struct sockaddr_in *)res->ai_addr)->sin_addr, ip, sizeof(ip));
    int64_t t_dns = now_ms();
    int s = socket(res->ai_family, res->ai_socktype, 0);
    if (s < 0 || connect(s, res->ai_addr, res->ai_addrlen) != 0) {
        printf("PROBE GET %s (%s): CONNECT FAIL errno=%d\n", host, ip, errno);
        if (s >= 0) close(s);
        freeaddrinfo(res);
        return;
    }
    freeaddrinfo(res);
    int64_t t_conn = now_ms();
    int n = snprintf(buf, sizeof(buf), "GET %s HTTP/1.0\r\nHost: %s\r\nUser-Agent: esp-idf net_probe\r\n\r\n", path, host);
    write(s, buf, n);
    struct timeval tv = { .tv_sec = 10 };
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    int total = 0, r;
    char status[64] = "";
    while ((r = read(s, buf, sizeof(buf) - 1)) > 0) {
        if (total == 0) {
            buf[r] = 0;
            char *eol = strstr(buf, "\r\n");
            int len = eol ? (int)(eol - buf) : r;
            if (len > (int)sizeof(status) - 1) len = sizeof(status) - 1;
            memcpy(status, buf, len);
            status[len] = 0;
        }
        total += r;
    }
    close(s);
    printf("PROBE GET %s%s -> %s | ip=%s bytes=%d dns_ms=%lld connect_ms=%lld total_ms=%lld\n",
           host, path, status[0] ? status : "(no response)", ip, total,
           t_dns - t0, t_conn - t_dns, now_ms() - t0);
}

static void outbound_task(void *arg)
{
    http_get("example.com", "80", "/");
    http_get(HOST_SERVER_IP, HOST_SERVER_PORT, "/hello.txt");
    printf("PROBE OUTBOUND DONE\n");
    vTaskDelete(NULL);
}

static int listen_on(int port)
{
    struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons(port), .sin_addr.s_addr = htonl(INADDR_ANY) };
    int ls = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    int opt = 1;
    setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    if (bind(ls, (struct sockaddr *)&a, sizeof(a)) != 0 || listen(ls, 2) != 0) {
        printf("PROBE LISTEN %d FAIL errno=%d\n", port, errno);
        close(ls);
        return -1;
    }
    printf("PROBE LISTEN %d OK\n", port);
    return ls;
}

static int accept_one(int ls, int port)
{
    struct sockaddr_in src;
    socklen_t sl = sizeof(src);
    int s = accept(ls, (struct sockaddr *)&src, &sl);
    if (s >= 0) {
        char ip[16];
        inet_ntoa_r(src.sin_addr, ip, sizeof(ip));
        printf("PROBE ACCEPT %d from %s:%d\n", port, ip, ntohs(src.sin_port));
        int one = 1;
        setsockopt(s, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
    }
    return s;
}

static void server_task(void *arg)
{
    int port = (int)arg;
    int ls = listen_on(port);
    if (ls < 0) { vTaskDelete(NULL); return; }
    static char bufs[3][1460];
    char *buf = bufs[port - 3333];
    for (;;) {
        int s = accept_one(ls, port);
        if (s < 0) continue;
        int64_t t0 = now_ms();
        long total = 0;
        int r;
        if (port == 3333) {                     /* echo */
            while ((r = recv(s, buf, 1460, 0)) > 0) {
                total += r;
                for (int off = 0; off < r;) {
                    int w = send(s, buf + off, r - off, 0);
                    if (w <= 0) { r = -1; break; }
                    off += w;
                }
                if (r < 0) break;
            }
            printf("PROBE ECHO done bytes=%ld ms=%lld\n", total, now_ms() - t0);
        } else if (port == 3334) {              /* sink */
            while ((r = recv(s, buf, 1460, 0)) > 0) total += r;
            int64_t ms = now_ms() - t0;
            printf("PROBE SINK done bytes=%ld ms=%lld\n", total, ms);
        } else {                                /* source */
            memset(buf, 'x', 1460);
            while (total < SOURCE_BYTES) {
                int chunk = SOURCE_BYTES - total < 1460 ? SOURCE_BYTES - total : 1460;
                int w = send(s, buf, chunk, 0);
                if (w <= 0) break;
                total += w;
            }
            printf("PROBE SOURCE done bytes=%ld ms=%lld\n", total, now_ms() - t0);
        }
        shutdown(s, SHUT_RDWR);
        close(s);
    }
}

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    int64_t t0 = now_ms();
    ESP_ERROR_CHECK(example_connect());   /* open_eth + DHCP (Kconfig: sdkconfig.qemu_eth) */

    esp_netif_ip_info_t ip;
    esp_netif_t *nif = esp_netif_get_handle_from_ifkey("ETH_DEF");
    if (nif && esp_netif_get_ip_info(nif, &ip) == ESP_OK) {
        esp_netif_dns_info_t dns;
        esp_netif_get_dns_info(nif, ESP_NETIF_DNS_MAIN, &dns);
        printf("PROBE NET UP ip=" IPSTR " gw=" IPSTR " mask=" IPSTR " dns=" IPSTR " connect_ms=%lld\n",
               IP2STR(&ip.ip), IP2STR(&ip.gw), IP2STR(&ip.netmask), IP2STR(&dns.ip.u_addr.ip4),
               now_ms() - t0);
    } else {
        ESP_LOGW(TAG, "no ETH_DEF netif?");
    }
    for (int p = 3333; p <= 3335; p++) xTaskCreate(server_task, "srv", 4096, (void *)p, 5, NULL);
    xTaskCreate(outbound_task, "outbound", 6144, NULL, 5, NULL);
}
