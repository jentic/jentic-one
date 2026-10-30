/**
 * The shell's review pop-up host, mounted once in the Layout: renders the
 * held-call review pop-up for `?review=` and the bulk-approve confirmation for
 * `?review_batch=` on any page (see `reviewLinks`). Every surface that offers a
 * review (the waiting card, the inbox rail, the bell, the attention toast)
 * opens one by setting the param, so this is the one place they render.
 *
 * Each is mounted only while its param is set, so closing one resets it. The
 * pop-up keeps its own sitting (the receipt of the last decision) while it
 * moves from call to call, and starts each call fresh; a different batch
 * remounts the confirmation.
 */
import { useEffect } from 'react';
import { useLocation } from 'react-router';
import { Button, Dialog, ErrorBoundary } from '@/shared/ui';
import { useReviewLinks } from '@/shared/approvals/reviewLinks';
import { BulkApproveDialog } from '@/shared/approvals/review/BulkApproveDialog';
import { HeldCallReview } from '@/shared/approvals/review/HeldCallReview';

/**
 * The host behind its own boundary: a call the surfaces can't render (a row
 * that isn't a held call) closes with a word, and never takes the shell down.
 * The boundary resets with the URL, so the next link starts clean.
 */
export function ApprovalReviewHost() {
	const { search } = useLocation();
	return (
		<ErrorBoundary resetKey={search} fallback={<ReviewHostFallback />}>
			<ReviewSurfaces />
		</ErrorBoundary>
	);
}

function ReviewHostFallback() {
	const { closeReview } = useReviewLinks();
	return (
		<Dialog
			open
			onClose={closeReview}
			size="sm"
			title="Couldn't show this call"
			footer={
				<Button variant="secondary" size="md" onClick={closeReview}>
					Close
				</Button>
			}
		>
			<p className="text-foreground-sub text-[13px] leading-relaxed">
				This call&apos;s details couldn&apos;t be displayed. Open it from the inbox instead.
			</p>
		</Dialog>
	);
}

function ReviewSurfaces() {
	const { reviewId, reviewAll, batchIds, dangling, advanceReview, closeReview, openReview } =
		useReviewLinks();
	// A param that names nothing (`?review=`, `?review_batch=,,`, an id that is
	// not one) opens nothing, and is stripped so it doesn't linger in the URL.
	useEffect(() => {
		if (dangling) closeReview();
	}, [dangling, closeReview]);
	if (reviewId) {
		return (
			<HeldCallReview
				heldId={reviewId}
				allAgents={reviewAll}
				onAdvance={advanceReview}
				onClose={closeReview}
			/>
		);
	}
	if (batchIds.length > 0) {
		return (
			<BulkApproveDialog
				key={batchIds.join(',')}
				heldIds={batchIds}
				onClose={closeReview}
				onReviewOne={openReview}
			/>
		);
	}
	return null;
}
