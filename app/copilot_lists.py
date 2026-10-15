"""Complete local pagination, outside the LLM token/claim budget."""
import hashlib
import json

from app.grounding import format_value


def collect_lists(context, plan, language, max_records=1000):
    if not plan.listing or plan.clarification or (plan.references and not plan.road_id):
        return [], []
    queries = []
    if 'connectivity' in plan.topics:
        queries.append(('settlements', 'settlement_connectivity', {'status': plan.status, 'road_id': plan.road_id,
                        'destination_type': plan.destination_type}))
    if 'infrastructure' in plan.topics:
        queries.extend((asset, 'infrastructure_impacts', {'asset_type': asset, 'only_exposed': True})
                       for asset in plan.assets or ['buildings', 'roads', 'bridges'])
    if plan.inventory_layer:
        queries.append((plan.inventory_layer, 'mapped_inventory', {'layer': plan.inventory_layer, 'destination_type': plan.destination_type}))
    lists, facts = [], []
    for layer, tool, arguments in queries:
        offset, rows, total = 0, [], None
        while len(rows) < max_records:
            result = context.call(tool, {**arguments, 'offset': offset, 'limit': min(5, max_records - len(rows))})
            total = result.total_count
            for identifier in result.feature_ids:
                row_facts = [f for f in result.facts if f.subject_id == identifier and f.subject_type == layer
                             and f.field_name in ('name', 'status', 'assessment_status')]
                facts.extend(row_facts)
                by_field = {f.field_name: f for f in row_facts}
                name = by_field.get('name')
                status = by_field.get('status') or by_field.get('assessment_status')
                rows.append({'feature_id': identifier, 'name': name.value if name and name.status == 'available' else identifier,
                             'status': format_value(status, language) if status else ('अभिलेखित नक्सा तत्व' if language == 'ne' else 'Mapped element'),
                             'fact_ids': [f.fact_id for f in row_facts]})
            if result.next_offset is None:
                break
            if result.next_offset <= offset:
                raise ValueError('Saved list pagination did not advance')
            offset = result.next_offset
        scope = hashlib.sha256(json.dumps([tool, arguments], sort_keys=True).encode()).hexdigest()[:20]
        count = context._add('queries.complete_list.' + scope + '.count', 'list_total_count', total,
                             ('Matching mapped elements for this list', 'यस सूचीसँग मिल्ने नक्साका तत्व'), unit='count',
                             interpretation='recorded_metadata', source_ids=[layer + '.geojson'],
                             method='Count of saved matches before pagination; selected filters are recorded in the list')
        context.returned_fact_ids.add(count.fact_id)
        facts.append(count)
        truncated = total is not None and len(rows) < total
        if total is None:
            summary = 'सूचीका लागि अभिलेखित तह उपलब्ध छैन।' if language == 'ne' else 'The saved layer for this list is unavailable.'
        elif language == 'ne':
            summary = f'{layer}: मिल्ने {total} मध्ये {len(rows)} अभिलेख देखाइएको छ।'
            summary += ' बाँकी अभिलेख पूर्ण तह निर्यातमा छन्।' if truncated else ' पूर्ण मिल्ने सूची तल छ।'
        else:
            summary = f'{layer.title()}: showing {len(rows)} of {total} matching mapped elements.'
            summary += ' Remaining records are available in the full layer export.' if truncated else ' The complete matching list is below.'
        lists.append({'layer': layer, 'filters': arguments, 'total_count': total, 'returned_count': len(rows),
                      'truncated': truncated, 'rows': rows, 'summary': summary + f' [{count.fact_id}]',
                      'export_url': f'/api/analyses/{context.run_id}/' + ('exports/settlements.csv' if layer == 'settlements' else f'files/{layer}.geojson')})
    return lists, list({f.fact_id: f for f in facts}.values())
