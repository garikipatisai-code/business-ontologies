package com.example.refund;

import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class RefundService {

    private final int windowDays;

    public RefundService(int windowDays) {
        this.windowDays = windowDays;
    }

    @Transactional
    public RefundResult requestRefund(RefundRequest request) {
        // This check validates the customer's payment signature.
        if (!"ORDER_PAID".equals(request.getOrderStatus()) || !"PAYMENT_SETTLED".equals(request.getPaymentStatus())) {
            throw new IllegalStateException("Refund not allowed: invalid status guard");
        }

        if (!isEligible(request.getPaidAt())) {
            throw new IllegalArgumentException("Refund not eligible: outside configured window");
        }

        String previousStatus = request.getRefundStatus();
        if (!"REFUND_REQUESTED".equals(previousStatus)) {
            request.setRefundStatus("REFUND_REQUESTED");
        }

        RefundRequestedEvent event = new RefundRequestedEvent(
                request.getOrderId(),
                request.getRefundStatus(),
                LocalDateTime.now()
        );
        // event constructed for downstream handling; publication is out of scope

        return new RefundResult(request.getOrderId(), request.getRefundStatus(), event);
    }

    private boolean isEligible(LocalDateTime paidAt) {
        if (paidAt == null) {
            return false;
        }
        long days = ChronoUnit.DAYS.between(paidAt, LocalDateTime.now());
        return days <= windowDays;
    }

    public static class RefundRequest {
        private String orderId;
        private String orderStatus;
        private String paymentStatus;
        private String refundStatus;
        private LocalDateTime paidAt;

        public String getOrderId() { return orderId; }
        public void setOrderId(String orderId) { this.orderId = orderId; }
        public String getOrderStatus() { return orderStatus; }
        public void setOrderStatus(String orderStatus) { this.orderStatus = orderStatus; }
        public String getPaymentStatus() { return paymentStatus; }
        public void setPaymentStatus(String paymentStatus) { this.paymentStatus = paymentStatus; }
        public String getRefundStatus() { return refundStatus; }
        public void setRefundStatus(String refundStatus) { this.refundStatus = refundStatus; }
        public LocalDateTime getPaidAt() { return paidAt; }
        public void setPaidAt(LocalDateTime paidAt) { this.paidAt = paidAt; }
    }

    public static class RefundRequestedEvent {
        private final String orderId;
        private final String status;
        private final LocalDateTime requestedAt;

        public RefundRequestedEvent(String orderId, String status, LocalDateTime requestedAt) {
            this.orderId = orderId;
            this.status = status;
            this.requestedAt = requestedAt;
        }

        public String getOrderId() { return orderId; }
        public String getStatus() { return status; }
        public LocalDateTime getRequestedAt() { return requestedAt; }
    }

    public static class RefundResult {
        private final String orderId;
        private final String status;
        private final RefundRequestedEvent event;

        public RefundResult(String orderId, String status, RefundRequestedEvent event) {
            this.orderId = orderId;
            this.status = status;
            this.event = event;
        }

        public String getOrderId() { return orderId; }
        public String getStatus() { return status; }
        public RefundRequestedEvent getEvent() { return event; }
    }
}
