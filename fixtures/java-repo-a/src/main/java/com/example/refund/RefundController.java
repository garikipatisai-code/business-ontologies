package com.example.refund;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class RefundController {

    private final RefundService refundService;

    public RefundController(RefundService refundService) {
        this.refundService = refundService;
    }

    @PostMapping("/refunds")
    public RefundService.RefundResult requestRefund(@RequestBody RefundService.RefundRequest request) {
        return refundService.requestRefund(request);
    }
}
