package com.example.orders;

import java.lang.reflect.Method;
import java.util.UUID;

public class OrderService {

    private final OrderClient orderClient;

    public OrderService(OrderClient orderClient) {
        this.orderClient = orderClient;
    }

    public Order createOrder(String customerId) {
        String orderId = UUID.randomUUID().toString();
        Order order = new Order(orderId, customerId, "CREATED");
        return order;
    }

    public void triggerPostCreateHook(String hookName) throws Exception {
        // deliberately unresolvable: dynamic method invocation with variable method name
        Method method = this.getClass().getMethod(hookName);
        method.invoke(this);
    }

    public String submitRefund(String orderId) {
        return orderClient.requestRefund(orderId);
    }

    public static class Order {
        private final String orderId;
        private final String customerId;
        private final String status;

        public Order(String orderId, String customerId, String status) {
            this.orderId = orderId;
            this.customerId = customerId;
            this.status = status;
        }

        public String getOrderId() { return orderId; }
        public String getCustomerId() { return customerId; }
        public String getStatus() { return status; }
    }
}
