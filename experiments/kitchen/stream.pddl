(define (stream kitchen)
    (:stream plan-motion-free
        :inputs (?start ?end)
        :fluents (atpose)
        :domain (and
            (conf ?start)
            (conf ?end)
        )
        :outputs (?t)
        :certified (and
            (mftraj ?t ?start ?end)
        )
    )
    (:stream plan-motion-holding
        :inputs (?item ?start ?end)
        :domain (and
            (conf ?start)
            (conf ?end)
            (item ?item)
        )
        :fluents (atpose)
        :outputs (?t)
        :certified (and
            (mhtraj ?item ?start ?end ?t)
        )
    )
    (:stream grasp-conf
        :inputs (?item ?pose)
        :outputs (?grasppose ?graspconf ?pregraspconf ?postgraspconf)
        :domain (and
            (item ?item)
            (pose ?item ?pose)
        )
        :certified (and
            (conf ?pregraspconf)
            (conf ?postgraspconf)
            (conf ?graspconf)
            (relpose ?item ?grasppose)
            (grasp ?item ?pose ?grasppose ?graspconf ?pregraspconf ?postgraspconf)
        )
    )
    (:stream placement-conf
        :inputs (?item ?region ?grasppose)
        :outputs (?placementpose ?placeconf ?preplaceconf ?postplaceconf)
        :domain (and
            (item ?item)
            (region ?region)
            (relpose ?item ?grasppose)
        )
        :certified (and
            (pose ?item ?placementpose)
            (conf ?preplaceconf)
            (conf ?postplaceconf)
            (conf ?placeconf)
            (contained ?item ?region ?placementpose)
            (place ?item ?region ?grasppose ?placementpose ?placeconf ?preplaceconf ?postplaceconf)
        )
    )
)