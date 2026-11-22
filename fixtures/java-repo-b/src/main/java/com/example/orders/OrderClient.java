package com.example.orders;

import org.springframework.web.client.RestTemplate;

public class OrderClient {

    private final RestTemplate restTemplate = new RestTemplate();

    public String requestRefund(String orderId) {
        String url = "http://refund-service.local/refunds";
        RefundPayload payload = new RefundPayload(orderId, "ORDER_PAID", "PAYMENT_SETTLED");
        return restTemplate.postForObject(url, payload, String.class);
    }

    public static class RefundPayload {
        private String orderId;
        private String orderStatus;
        private String paymentStatus;

        public RefundPayload(String orderId, String orderStatus, String paymentStatus) {
            this.orderId = orderId;
            this.orderStatus = orderStatus;
            this.paymentStatus = paymentStatus;
        }

        public String getOrderId() { return orderId; }
        public String getOrderStatus() { return orderStatus; }
        public String getPaymentStatus() { return paymentStatus; }
    }
}
