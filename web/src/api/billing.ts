/** Plans, checkout and subscription state. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { get, post, qs } from './client';
import { keys } from './hooks';
import type { components } from './schema';

export type Plan = components['schemas']['PlanOut'];
export type Provider = components['schemas']['ProviderOut'];
export type Plans = components['schemas']['PlansOut'];
export type Checkout = components['schemas']['CheckoutOut'];
export type Subscription = components['schemas']['SubscriptionOut'];
export type Payment = components['schemas']['PaymentOut'];
export type DiscountPreview = components['schemas']['DiscountPreviewOut'];

export const billingKeys = {
  plans: ['billing', 'plans'] as const,
  subscription: ['billing', 'subscription'] as const,
  payments: ['billing', 'payments'] as const,
};

export function usePlans() {
  return useQuery({ queryKey: billingKeys.plans, queryFn: () => get<Plans>('/v1/plans') });
}

export function useSubscription() {
  return useQuery({
    queryKey: billingKeys.subscription,
    queryFn: () => get<Subscription>('/v1/me/subscription'),
  });
}

export function usePayments() {
  return useQuery({ queryKey: billingKeys.payments, queryFn: () => get<Payment[]>('/v1/me/payments') });
}

export interface CheckoutInput {
  plan_code: string;
  provider: string;
  discount_code?: string | null;
}

export function useCheckout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CheckoutInput) => post<Checkout>('/v1/payments/checkout', input),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: billingKeys.payments });
    },
  });
}

export function useStartTrial() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => post<Subscription>('/v1/payments/trial', {}),
    onSuccess: (subscription) => {
      client.setQueryData(billingKeys.subscription, subscription);
      void client.invalidateQueries({ queryKey: keys.me });
      void client.invalidateQueries({ queryKey: billingKeys.plans });
    },
  });
}

/** Server-side check, so the price shown is always the price charged. */
export function useDiscountPreview() {
  return useMutation({
    mutationFn: (input: { code: string; plan_code: string; currency: string }) =>
      get<DiscountPreview>(`/v1/payments/discount${qs({ ...input })}`),
  });
}

/** Refetches the subscription after a payment completes outside the app. */
export function useRefreshBilling(): () => void {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: billingKeys.subscription });
    void client.invalidateQueries({ queryKey: billingKeys.payments });
    void client.invalidateQueries({ queryKey: billingKeys.plans });
    void client.invalidateQueries({ queryKey: keys.me });
  };
}
